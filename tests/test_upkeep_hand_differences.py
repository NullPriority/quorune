"""Pinned CR107.1b/120.8/608.2h: current public hand count at resolution."""
from dataclasses import FrozenInstanceError,replace
from copy import deepcopy
from types import SimpleNamespace as Row
import json,unittest
from quorune.upkeep_hand_difference_model import UpkeepHandDifferenceSpec as Difference,HandDifferenceDirection as Direction
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec as Scalar,ScalarAmountOrigin as Origin
from quorune.query_effect_amount_model import PublicQueryAmountError


class UpkeepHandDifferenceModelTests(unittest.TestCase):
    def test_two_fixed_arithmetic_forms_are_immutable_and_round_trip(self):
        for direction,constant in ((Direction.HAND_MINUS_CONSTANT,4),(Direction.CONSTANT_MINUS_HAND,3)):
            spec=Difference(direction,constant)
            self.assertEqual(spec,Difference.from_dict(json.loads(json.dumps(spec.to_dict()))))
            with self.assertRaises(FrozenInstanceError):spec.constant=1
    def test_zero_negative_and_positive_boundaries_follow_independent_rules_table(self):
        counts=(0,2,3,4,5,7)
        self.assertEqual((0,0,0,0,1,3),tuple(Difference(Direction.HAND_MINUS_CONSTANT,4).evaluate(n) for n in counts))
        self.assertEqual((3,1,0,0,0,0),tuple(Difference(Direction.CONSTANT_MINUS_HAND,3).evaluate(n) for n in counts))
    def test_unknown_versions_constants_directions_and_invalid_counts_reject(self):
        raw=Difference(Direction.HAND_MINUS_CONSTANT,4).to_dict()
        for changed in ({'schema_version':True},{'schema_version':2},{'constant':True},{'constant':3},
                        {'constant':-4},{'direction':'sum'},{'extra':1}):
            with self.assertRaises((ValueError,TypeError)):Difference.from_dict({**raw,**changed})
        for n in (-1,True,1.0,None):
            with self.assertRaises(ValueError):Difference(Direction.CONSTANT_MINUS_HAND,3).evaluate(n)

    def test_new_scalar14_round_trip_is_explicit_and_old_target_and_source_wire_shapes_retain(self):
        amount=Difference(Direction.HAND_MINUS_CONSTANT,4).scalar_amount();new=Scalar.from_dict(amount)
        self.assertEqual(amount,new.to_dict());self.assertIs(new.origin,Origin.UPKEEP_HAND_DIFFERENCE)
        self.assertEqual(14,new.schema_version);self.assertIsNone(new.binding_id)
        for old in (Scalar(Origin.SOURCE,characteristic='power'),Scalar(Origin.TARGET_HAND_COUNT,schema_version=4),
                    Scalar(Origin.TARGET_HAND_COUNT,schema_version=7,target_group='target_0')):
            raw=old.to_dict();self.assertNotIn('hand_difference',raw);self.assertEqual(raw,Scalar.from_dict(raw).to_dict())
        for changed in ({'schema_version':1},{'coefficient':-1},{'coefficient':True},
                        {'binding_id':'cached-before-resolution'},{'characteristic':'power'},{'extra':True}):
            with self.assertRaises((ValueError,TypeError)):Scalar.from_dict({**amount,**changed})


from quorune.rules.upkeep_hand_differences import upkeep_hand_difference_program_dependencies,is_closed_upkeep_hand_difference_program
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
from quorune.upkeep_hand_difference_model import UPKEEP_HAND_DIFFERENCE_CAPABILITY as CAP


def pure_frame(count=6,direction=Direction.HAND_MINUS_CONSTANT):
    difference=Difference(direction,4 if direction is Direction.HAND_MINUS_CONSTANT else 3)
    amount=difference.scalar_amount()
    effect={'op':'damage','source':'$source','target':'$context.player','amount':amount}
    program=Row(event='step.begin',active_zone='battlefield',target_schema=None,handlers=(),effects=(effect,),coverage=(),
                event_condition={'all':[{'field':'step','op':'eq','value':'upkeep'},
                                        {'field':'player','op':'ne','value':'$source.controller'}]},capability_dependencies=())
    item=Row(semantic_key='only-program',kind='triggered_ability',targets=[],controller='A',
             context={'event':'step.begin','step':'upkeep','player':'B'})
    host=Row(semantics=Row(get=lambda key:program if key=='only-program' else None),
             state=Row(step='upkeep',active_player='B',players={
                 'A':Row(in_game=True,zones={'hand':['unrelated']*20}),
                 'B':Row(in_game=True,zones={'hand':['private']*count})}))
    return host,item,program,difference,amount


class UpkeepHandDifferenceFrameTests(unittest.TestCase):
    def test_controller_surplus_runtime_rejects_changed_carrier_and_timing(self):
        from quorune.rules.upkeep_hand_differences import controller_upkeep_hand_surplus_condition

        host, item, program, _, amount = pure_frame()
        item.controller = "B"
        program.event_condition = controller_upkeep_hand_surplus_condition()
        effect = {"op": "life", "player": "$context.player", "delta": amount}
        program.effects = (effect,)
        required = upkeep_hand_difference_program_dependencies(program)
        self.assertIn("life.change.effect", required)
        self.assertEqual(2, resolve_scalar_effect_amount(host, amount, item))
        for changed in (
            {**effect, "player": "$controller"},
            {**effect, "op": "lose_life"},
            {**effect, "delta": Difference(Direction.CONSTANT_MINUS_HAND, 3).scalar_amount()},
        ):
            program.effects = (changed,)
            with self.assertRaises(ValueError):
                upkeep_hand_difference_program_dependencies(program)
        program.effects = (effect,)
        item.controller = "A"
        with self.assertRaises(PublicQueryAmountError):
            resolve_scalar_effect_amount(host, amount, item)

    def test_reads_current_bound_public_count_without_targets_or_context_cache(self):
        host,item,program,_,amount=pure_frame();before=deepcopy(item.context)
        self.assertEqual(2,resolve_scalar_effect_amount(host,amount,item))
        host.state.players['B'].zones['hand'].append('another-private-card')
        self.assertEqual(3,resolve_scalar_effect_amount(host,amount,item));self.assertEqual(before,item.context)
        required=upkeep_hand_difference_program_dependencies(program);self.assertIn(CAP,required)
        program.capability_dependencies=required;self.assertTrue(is_closed_upkeep_hand_difference_program(program))
        program.capability_dependencies=tuple(c for c in required if c!=CAP)
        self.assertFalse(is_closed_upkeep_hand_difference_program(program))

    def test_wrong_event_clock_actor_targets_and_amount_substitution_fail_before_mutation(self):
        for change in ({'kind':'spell'},{'targets':['B']},{'controller':'B'},
                       {'context':{'event':'step.begin','step':'draw','player':'B'}},
                       {'context':{'event':'spell.cast','step':'upkeep','player':'B'}},
                       {'context':{'event':'step.begin','step':'upkeep','player':'unknown'}}):
            host,item,_,_,amount=pure_frame();before=deepcopy(host.state.players['B'].zones)
            for key,value in change.items():setattr(item,key,value)
            with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(host,amount,item)
            self.assertEqual(before,host.state.players['B'].zones)
        host,item,_,_,amount=pure_frame();host.state.active_player='A'
        with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(host,amount,item)
        host,item,_,_,_=pure_frame()
        with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(host,Difference(Direction.CONSTANT_MINUS_HAND,3).scalar_amount(),item)

    def test_raw_frame_minima_do_not_depend_on_coverage_and_forbidden_carriers_stay_closed(self):
        # Existing composition views expose effects and omit handlers. The
        # new-origin scanner must not change their legacy closure behavior.
        self.assertEqual((),upkeep_hand_difference_program_dependencies(Row(effects=({'op':'draw','count':1},))))
        for change in ({'event':'spell.cast'},{'active_zone':'hand'},{'target_schema':{'zones':['player']}},
                       {'event_condition':{'all':[{'field':'step','op':'eq','value':'upkeep'}]}}):
            host,item,program,_,_=pure_frame()
            for key,value in change.items():setattr(program,key,value)
            with self.assertRaises(ValueError):upkeep_hand_difference_program_dependencies(program)
        _,_,program,_,amount=pure_frame();program.handlers=({'nested':amount},)
        with self.assertRaises(ValueError):upkeep_hand_difference_program_dependencies(program)
        host,item,_,_,amount=pure_frame();host.state.players['B'].in_game=False
        self.assertEqual(0,resolve_scalar_effect_amount(host,amount,item))


from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


def upkeep_record(text,*,noun='artifact'):
    return CardRecord(oracle_id='fixture:upkeep-hand-difference',name='Generic Upkeep Difference',mana_cost='{3}',mana_value=3.0,
      type_line='Artifact Creature — Construct' if noun=='creature' else 'Artifact',oracle_text=text,
      power='2' if noun=='creature' else None,toughness='2' if noun=='creature' else None,loyalty=None,defense=None,colors=(),color_identity=(),
      keywords=(),produced_mana=(),layout='normal',released_at='2026-01-01',legalities={'commander':'legal'},faces=(),raw={})


class UpkeepHandDifferenceCompilerTests(unittest.TestCase):
    def test_controller_hand_count_only_admits_exact_upkeep_life_result(self):
        registry = load_default_capability_registry()
        body = "At the beginning of your upkeep, you gain X life, where X is the number of cards in your hand minus 4."
        compile_body = lambda text: compile_oracle_card(
            upkeep_record(text), capability_registry=registry, capability_profile="commander_review",
        )
        ir = compile_body(body)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual(("step.begin", "battlefield", None), (node.event, node.active_zone, node.target_schema))
        self.assertEqual(("life", "$context.player"), (effect["op"], effect["player"]))
        self.assertEqual(Difference(Direction.HAND_MINUS_CONSTANT, 4), Scalar.from_dict(effect["delta"]).hand_difference)
        self.assertIn(CAP, node.capability_dependencies)
        self.assertIn("life.change.effect", node.capability_dependencies)
        for variant in (
            body.replace("your upkeep", "your draw step"),
            body.replace("your upkeep", "each player's upkeep"),
            body.replace("your hand", "their hand"),
            body.replace("minus 4", "minus 3"),
            body.replace("gain X life", "lose X life"),
            body + " Draw a card.",
        ):
            with self.subTest(variant=variant):
                changed = compile_body(variant)
                self.assertNotIn(CAP, {cap for face in changed.faces for item in face.nodes for cap in item.capability_dependencies})

    def test_both_directions_and_both_source_nouns_lower_to_one_nontargeted_scheduled_scalar(self):
        registry=load_default_capability_registry()
        for noun in ('artifact','creature'):
            for words,direction,constant in (('the number of cards in their hand minus 4',Direction.HAND_MINUS_CONSTANT,4),
                                              ('3 minus the number of cards in their hand',Direction.CONSTANT_MINUS_HAND,3)):
                text=f"At the beginning of each opponent's upkeep, this {noun} deals X damage to that player, where X is {words}."
                ir=compile_oracle_card(upkeep_record(text,noun=noun),capability_registry=registry,capability_profile='commander_review')
                node=ir.faces[0].nodes[0];self.assertTrue(node.lowerable);self.assertIsNone(node.target_schema)
                self.assertEqual('step.begin',node.event);self.assertEqual(1,len(node.effects))
                effect=node.effects[0];self.assertEqual(('$source','$context.player'),(effect['source'],effect['target']))
                self.assertEqual(Difference(direction,constant),Scalar.from_dict(effect['amount']).hand_difference)
                self.assertIn(CAP,node.capability_dependencies);self.assertIn('scheduled_player_departure_v1',node.runtime_coverage)

    def test_other_steps_players_offsets_and_extra_riders_do_not_lower_this_quantity(self):
        good="At the beginning of each opponent's upkeep, this artifact deals X damage to that player, where X is 3 minus the number of cards in their hand."
        for text in (good.replace('upkeep','draw step'),good.replace("opponent's","player's"),
                     good.replace("each opponent's","the chosen player's"),good.replace('is 3 minus','is 4 minus'),
                     good+' Draw a card.'):
            ir=compile_oracle_card(upkeep_record(text),capability_registry=load_default_capability_registry())
            self.assertNotIn(CAP,{c for face in ir.faces for node in face.nodes for c in node.capability_dependencies})

    def test_existing_optional_composition_does_not_make_a_forbidden_scalar_frame_runtime_ready(self):
        text="At the beginning of each opponent's upkeep, you may have this artifact deal X damage to that player, where X is 3 minus the number of cards in their hand."
        ir=compile_oracle_card(upkeep_record(text),capability_registry=load_default_capability_registry())
        node=next(n for face in ir.faces for n in face.nodes if CAP in n.capability_dependencies)
        program=Row(event=node.event,active_zone=node.active_zone,target_schema=node.target_schema,handlers=node.handlers,
                    effects=node.effects,event_condition=node.event_condition,coverage=node.runtime_coverage,capability_dependencies=node.capability_dependencies)
        self.assertEqual('offer_optional_effect',node.effects[0]['op'])
        with self.assertRaises(ValueError):upkeep_hand_difference_program_dependencies(program)
        self.assertFalse(is_closed_upkeep_hand_difference_program(program))


from pathlib import Path
import tempfile
from dataclasses import replace
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import register_generated_programs
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from quorune.record import authoritative_state_hash

FIXTURE=ROOT/'tests/fixtures/upkeep-hand-difference-cards.json'
CANDIDATES=('Iron Maiden','Viseling','Rackling','Wheel of Torture')
UNPAIRED_CONTROLS=('Black Vise','The Rack')
RETENTION=("Misers' Cage","Paupers' Cage",'Sudden Impact','Storm Seeker')


class UpkeepHandDifferenceRegistrationTests(unittest.TestCase):
    def test_four_complete_originals_close_normally_targeted_and_conditional_controls_retain_and_chosen_opponent_stays_unready(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'originals.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,*UNPAIRED_CONTROLS,*RETENTION):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        if name in UNPAIRED_CONTROLS:
                            card = replace(card, oracle_text=card.oracle_text.split("\n", 1)[-1])
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name not in UNPAIRED_CONTROLS,bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])


class UpkeepHandDifferenceGameplayTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'upkeep-hand-difference.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Upkeep hand differences',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])
    def start(self,seed):
        s=self.session(seed)
        for player in s.state.players.values():
            for oid in tuple(player.zones['hand']):
                player.zones['hand'].remove(oid);player.zones['library'].append(oid);s.state.cards[oid].zone='library'
        return s
    def hand(self,s,seat):return [s.state.cards[oid] for oid in s.state.players[seat].zones['hand']]
    def fill(self,s,seat,count):
        return [self.add(s.engine,'Plains',seat=seat,zone='hand',ref=f'difference-hand-{seat}-{i}') for i in range(count)]
    def lands(self,s,seat,name,count):
        return [self.add(s.engine,name,seat=seat,ref=f'difference-mana-{seat}-{name}-{i}') for i in range(count)]
    def reject(self,s,command,seat='D'):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))
    def cast_closed(self,s,seat,card,targets=(),*,top_only=False):
        self.assert_closed(s,card);self.priority(s,seat)
        self.reject(s,{'action_id':'cast:'+s.state.cards[card.object_id].ref,'targets':list(targets),'pay':'auto'},'D' if seat!='D' else 'C')
        self.cast(s,seat,card,targets)
        if top_only:self.resolve_top(s)
        else:self.assertIsNone(self.resolve(s))
    def upkeep(self,s,seat):
        self.until(s,lambda game:game.state.active_player==seat and game.state.step=='upkeep')
    def trigger(self,s,source):
        return next(item for item in s.state.stack if item.kind=='triggered_ability' and item.source_object_id==source.object_id)
    def scene(self,name,count,seed,*,response=None,hexproof=False,control=False):
        s=self.start(seed);source=self.add(s.engine,name,zone='hand');self.fill(s,'B',count)
        extra={}
        if response:
            extra[response]=self.add(s.engine,response,seat='B',zone='hand',ref='difference-response-'+response)
            self.lands(s,'B','Island' if response=='Opportunity' else 'Plains',6 if response=='Opportunity' else 2)
        if hexproof:
            ward=self.add(s.engine,'Witchbane Orb',seat='B');self.assert_closed(s,ward)
        if control:
            extra['Donate']=self.add(s.engine,'Donate',zone='hand');self.add(s.engine,'Vedalken Orrery')
            self.lands(s,'A','Island',3)
        secret=self.add(s.engine,'Shock',seat='C',zone='hand',ref='difference-private-C')
        self.prepare(s,mana={'C':4 if name in ('Viseling','Rackling') else 3});self.checkpoint(s)
        self.cast_closed(s,'A',source);self.upkeep(s,'B');return s,source,extra,secret
    def privacy(self,s,secret):
        for principal in ('pilot:A','pilot:B','pilot:D','spectator'):
            packet=str(s.packet(principal,full=True));self.assertNotIn(secret.ref,packet);self.assertNotIn(secret.object_id,packet)
        for principal in ('pilot:A','pilot:C','pilot:D','spectator'):
            packet=str(s.packet(principal,full=True))
            for card in self.hand(s,'B'):
                self.assertNotIn(card.ref,packet);self.assertNotIn(card.object_id,packet)

    def test_actual_four_original_casts_and_zero_or_positive_damage_use_upkeep_before_normal_draw(self):
        for i,name in enumerate(CANDIDATES):
            for count in (0,7):
                with self.subTest(name=name,count=count):
                    s,source,_,secret=self.scene(name,count,934010+i*10+count)
                    item=self.trigger(s,source);self.assertEqual('B',item.context['player']);self.assertEqual([],item.targets)
                    self.assertEqual(count,len(self.hand(s,'B')));draws=len(s.state.players['B'].draw_history)
                    self.privacy(s,secret);s=self.replay(s,load=True)
                    amount=max(0,count-4) if name in ('Iron Maiden','Viseling') else max(0,3-count)
                    with patch.object(s.engine,'_dispatch_semantic_event',wraps=s.engine._dispatch_semantic_event) as dispatched:
                        self.assertIsNone(self.resolve(s))
                    damage=[call.args[1] for call in dispatched.call_args_list if call.args[0]=='damage.dealt']
                    self.assertEqual([amount] if amount else [],[event['amount'] for event in damage])
                    self.assertEqual(40-amount,s.state.players['B'].life);self.assertEqual(draws,len(s.state.players['B'].draw_history))
                    self.replay(s,load=True)

    def test_actual_opportunity_response_changes_current_hand_count_before_artifact_damage(self):
        s,source,cards,secret=self.scene('Iron Maiden',2,934100,response='Opportunity')
        self.assertEqual(3,len(self.hand(s,'B')));self.trigger(s,source);self.cast_closed(s,'B',cards['Opportunity'],['B'],top_only=True)
        self.assertEqual(6,len(self.hand(s,'B')));self.assertEqual(40,s.state.players['B'].life)
        self.privacy(s,secret);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_disenchant_response_reduces_hand_count_and_departed_creature_deals_lki_damage(self):
        s,source,cards,secret=self.scene('Rackling',2,934110,response='Disenchant')
        self.assertEqual(3,len(self.hand(s,'B')));old=s.state.cards[source.object_id].logical_object_id
        self.cast_closed(s,'B',cards['Disenchant'],[source.ref],top_only=True)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(2,len(self.hand(s,'B')))
        self.privacy(s,secret);s=self.replay(s,load=True)
        with patch.object(s.engine,'_dispatch_semantic_event',wraps=s.engine._dispatch_semantic_event) as dispatched:
            self.assertIsNone(self.resolve(s))
        damage=[call.args[1] for call in dispatched.call_args_list if call.args[0]=='damage.dealt']
        self.assertEqual([old],[event['source_logical_object_id'] for event in damage])
        self.assertEqual(39,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_hexproof_does_not_target_and_source_control_change_keeps_old_upkeep_subject(self):
        for protected in (False,True):
            with self.subTest(hexproof=protected):
                s,source,cards,secret=self.scene('Iron Maiden',6,934120+protected,hexproof=protected,control=True)
                item=self.trigger(s,source);self.assertEqual([],item.targets);self.assertEqual('A',item.controller)
                # Donate targets a player; the upkeep damage does not. Give
                # to C when B is protected, and directly to B in the other case.
                recipient='C' if protected else 'B'
                self.cast_closed(s,'A',cards['Donate'],[recipient,source.ref],top_only=True)
                self.assertEqual(recipient,s.state.cards[source.object_id].controller);self.assertEqual('A',item.controller)
                self.privacy(s,secret);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_each_other_upkeep_binds_its_player_and_own_upkeep_never_triggers(self):
        s=self.start(934130);source=self.add(s.engine,'Wheel of Torture',zone='hand')
        self.fill(s,'B',2);self.fill(s,'C',1);self.prepare(s,mana={'C':3});self.checkpoint(s);self.cast_closed(s,'A',source)
        for seat,loss in (('B',1),('C',2),('D',3)):
            self.upkeep(s,seat);self.assertEqual(seat,self.trigger(s,source).context['player'])
            self.assertIsNone(self.resolve(s));self.assertEqual(40-loss,s.state.players[seat].life)
        self.upkeep(s,'A');self.assertFalse(any(item.source_object_id==source.object_id for item in s.state.stack));self.assertEqual(40,s.state.players['A'].life)
        self.replay(s,load=True)

    def test_actual_departed_upkeep_player_pending_trigger_is_skipped_after_reload(self):
        s,source,_,_=self.scene('Wheel of Torture',0,934140);self.trigger(s,source)
        self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.players['B'].in_game);self.replay(s,load=True)

    def test_actual_source_humble_before_upkeep_suppresses_its_printed_trigger(self):
        # Keep the source humbled across the turn boundary: Humble itself
        # expires at end of turn, whereas this Aura remains attached.
        s=self.start(934145);source=self.add(s.engine,'Rackling',zone='hand');humble=self.add(s.engine,"Kasmina's Transmutation",zone='hand')
        self.lands(s,'A','Island',2);self.prepare(s,mana={'C':4});self.checkpoint(s)
        self.cast_closed(s,'A',source);self.cast_closed(s,'A',humble,[source.ref]);self.upkeep(s,'B')
        self.assertFalse(any(item.source_object_id==source.object_id for item in s.state.stack))
        self.assertEqual(40,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_post_damage_event_fault_rolls_back_quantity_result_and_retries(self):
        from quorune.errors import GameRuleError
        s,_,_,_=self.scene('Wheel of Torture',0,934146);s=self.replay(s,load=True)
        original=s.engine._dispatch_semantic_event;observed=[];failed=None
        def fault(event,context,**kwargs):
            result=original(event,context,**kwargs)
            if event=='damage.dealt':
                observed.append(s.state.players['B'].life)
                raise GameRuleError('Injected upkeep hand-difference post-damage event failure')
            return result
        with patch.object(s.engine,'_dispatch_semantic_event',side_effect=fault):
            for _ in range(8):
                self.assertEqual('priority',s.state.pending_decision.kind)
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([37],observed);self.assertIsNotNone(failed);self.assertEqual(40,s.state.players['B'].life)
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_registered_payload_requires_fine_scope_without_any_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.start(934150);source=self.add(s.engine,'Iron Maiden');self.assert_closed(s,source)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        deps=tuple(c for c in program.capability_dependencies if c!=CAP)
        missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_fresh_trigger_time_quantity_mutant_is_killed_by_actual_opportunity_response(self):
        from quorune.rules import upkeep_hand_differences as owned
        with patch.object(owned,'resolve_upkeep_hand_difference',return_value=0):
            with self.assertRaises(AssertionError):self.test_actual_opportunity_response_changes_current_hand_count_before_artifact_damage()

    def ivory_tower_scene(self, seed, *, hand_at_trigger, response):
        game = self.start(seed)
        source = self.add(game.engine, "Ivory Tower", zone="hand")
        instant = self.add(game.engine, response, zone="hand", ref="ivory-response")
        self.fill(game, "A", hand_at_trigger - 1)
        self.lands(game, "A", "Island" if response == "Opportunity" else "Plains",
                   6 if response == "Opportunity" else 2)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.cast_closed(game, "A", source)
        self.upkeep(game, "B")
        self.assertFalse(any(entry.source_object_id == source.object_id for entry in game.state.stack))
        self.upkeep(game, "A")
        trigger = self.trigger(game, source)
        self.assertEqual(("A", "A", []), (trigger.controller, trigger.context["player"], trigger.targets))
        self.assertEqual(hand_at_trigger, len(self.hand(game, "A")))
        return game, source, instant

    def assert_ivory_tower_private_hand(self, game):
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            for card in self.hand(game, "A"):
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)

    def assert_ivory_tower_response_uses_resolved_hand(self):
        game, _, response = self.ivory_tower_scene(934201, hand_at_trigger=4, response="Opportunity")
        self.assert_ivory_tower_private_hand(game)
        self.cast_closed(game, "A", response, ["A"], top_only=True)
        self.assertEqual(7, len(self.hand(game, "A")))
        self.assertEqual(40, game.state.players["A"].life)
        self.assert_ivory_tower_private_hand(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((43, 40, 40, 40), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.assert_ivory_tower_private_hand(game)
        self.replay(game, load=True)

    def test_actual_ivory_tower_reads_current_private_hand_count(self):
        self.assert_ivory_tower_response_uses_resolved_hand()

    def test_actual_ivory_tower_keeps_upkeep_recipient_after_source_departs(self):
        for hand_at_trigger, expected_gain in ((5, 0), (6, 1)):
            with self.subTest(hand_at_trigger=hand_at_trigger):
                game, source, response = self.ivory_tower_scene(
                    934210 + hand_at_trigger, hand_at_trigger=hand_at_trigger, response="Disenchant",
                )
                self.cast_closed(game, "A", response, [source.ref], top_only=True)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual(hand_at_trigger - 1, len(self.hand(game, "A")))
                self.assert_ivory_tower_private_hand(game)
                game = self.replay(game, load=True)
                with patch.object(game.engine, "_dispatch_semantic_event", wraps=game.engine._dispatch_semantic_event) as dispatch:
                    self.assertIsNone(self.resolve(game))
                life_gains = [call for call in dispatch.call_args_list if call.args[0] == "life.gained"]
                self.assertEqual(int(expected_gain > 0), len(life_gains))
                self.assertEqual((40 + expected_gain, 40, 40, 40), tuple(game.state.players[seat].life for seat in "ABCD"))
                self.replay(game, load=True)

    def test_ivory_tower_response_witness_rejects_a_trigger_time_quantity(self):
        from quorune.rules import upkeep_hand_differences

        with patch.object(upkeep_hand_differences, "resolve_upkeep_hand_difference", return_value=0):
            with self.assertRaisesRegex(AssertionError, r"\(43, 40, 40, 40\) != \(40, 40, 40, 40\)"):
                self.assert_ivory_tower_response_uses_resolved_hand()
