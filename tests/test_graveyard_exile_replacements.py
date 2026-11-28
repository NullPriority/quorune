"""CR614/616/730: pre-move subject scope remains independent of destination."""
from dataclasses import asdict,FrozenInstanceError
from types import SimpleNamespace
import json,unittest
from common import ROOT
from quorune.graveyard_exile_model import (
    GraveyardExileSpec,GraveyardExileScope as Scope,GRAVEYARD_EXILE_HANDLER as HANDLER,
)
from quorune.zone_object_qualifications import ZONE_OBJECT_QUALIFICATIONS_V1 as FACTS,zone_object_qualifications_v1
from quorune.compiler.graveyard_exile_templates import graveyard_exile_template
from quorune.replacement.model import ReplaceableEvent,AffectedObject
from quorune.replacement.applicability import condition_matches
from quorune.replacement.composition_scope import card_only_replacement

FIXTURE=ROOT/'tests/fixtures/graveyard-exile-replacement-cards.json'
CANDIDATES=('Rest in Peace','Leyline of the Void','Samurai of the Pale Curtain','Anafenza, the Foremost','Dryad Militant','Stone of Erech')
TEXTS=(
 'If a card or token would be put into a graveyard from anywhere, exile it instead.',
 "If a card would be put into an opponent's graveyard from anywhere, exile it instead.",
 'If a permanent would be put into a graveyard, exile it instead.',
 "If a nontoken creature an opponent owns would die or a creature card not on the battlefield would be put into an opponent's graveyard, exile that card instead.",
 'If an instant or sorcery card would be put into a graveyard from anywhere, exile it instead.',
 'If a creature an opponent controls would die, exile it instead.',
)

class GraveyardExileModelTests(unittest.TestCase):
    def test_six_generic_subjects_have_exact_closed_immutable_descriptors(self):
        for scope,text in zip(Scope,TEXTS):
            spec=GraveyardExileSpec(scope);compiled=graveyard_exile_template(text)
            self.assertEqual(spec.descriptor(),compiled[1]);self.assertEqual(spec,GraveyardExileSpec.from_descriptor(json.loads(json.dumps(compiled[1]))))
            self.assertEqual({'scope':scope,'schema_version':1},asdict(spec))
            with self.assertRaises(FrozenInstanceError):spec.scope=Scope.PERMANENT
    def test_unrepresented_results_subjects_and_old_counter_shape_stay_closed(self):
        for text in (TEXTS[0]+' Draw a card.',TEXTS[1].replace('exile it instead.','instead exile it with a void counter on it.'),
                TEXTS[2].replace('permanent','creature'),TEXTS[5].replace('controls','owns'),
                TEXTS[0].replace('exile it instead.','put it into your hand instead.'),
                'If a nontoken creature an opponent controls would die, instead exile that card and create a 2/2 black Zombie creature token.'):
            self.assertIsNone(graveyard_exile_template(text))
        raw=GraveyardExileSpec(Scope.CARD_OR_TOKEN).descriptor()
        for change in ({'schema_version':True},{'schema_version':2},{'event':'creature.dies'},
                {'destination':'hand'},{'counters':{}},{'subject':{'schema_version':1,'scope':'unknown'}},
                {'subject':{'schema_version':True,'scope':'card_or_token'}},{'handler_id':'replacement.zone.destination.v1'}):
            with self.assertRaises((ValueError,TypeError)):GraveyardExileSpec.from_descriptor({**raw,**change})
    def event(self,kind='card',origin='battlefield',types=('creature',),owner='B',controller='B',destination='graveyard'):
        return ReplaceableEvent(event_id='event',kind='zone.change',affected_player=None,
            affected_object=AffectedObject(object_id='object',owner=owner,controller=controller),
            payload={'origin':origin,'destination':destination,'object_kind':kind,'object_types':list(types),'owner':owner})
    def test_derived_facts_do_not_mutate_or_expand_historical_event_payload(self):
        for event,expected in ((self.event(),{'card','card_or_token','permanent','creature_permanent','nontoken_creature_permanent'}),
                (self.event(kind='noncard'),{'card_or_token','permanent','creature_permanent'}),
                (self.event(origin='hand',controller=None),{'card','card_or_token','creature_card_not_battlefield'}),
                (self.event(kind='noncard',origin='stack',types=('instant',)),set())):
            before=event.to_dict();self.assertEqual(expected,set(zone_object_qualifications_v1(event)));self.assertEqual(before,event.to_dict());self.assertNotIn(FACTS,event.payload)
    def test_bad_or_spoofed_new_fact_inputs_reject_without_changing_old_event_fields(self):
        event=self.event()
        for payload in ({**dict(event.payload),FACTS:['card_or_token']},{**dict(event.payload),'object_kind':'token'},
                {**dict(event.payload),'origin':None},{**dict(event.payload),'object_types':'creature'},
                {**dict(event.payload),'object_types':['Creature']}):
            with self.assertRaises(ValueError):zone_object_qualifications_v1(SimpleNamespace(kind='zone.change',payload=payload))
    def test_missing_controller_or_inconsistent_owner_in_new_subject_data_fails_closed(self):
        for event in (self.event(controller=None),self.event()):
            payload=dict(event.payload)
            if event.affected_object.controller is not None:payload['owner']='C'
            malformed=SimpleNamespace(kind=event.kind,payload=payload,affected_object=event.affected_object)
            with self.assertRaises(ValueError):zone_object_qualifications_v1(malformed)
    def test_card_only_component_scope_is_distinct_from_whole_nontoken_and_all_object_scope(self):
        for scope in Scope:
            effect=SimpleNamespace(event_kind='zone.change',conditions=GraveyardExileSpec(scope).conditions('A'))
            self.assertEqual(scope in (Scope.OPPONENT_CARD,Scope.INSTANT_OR_SORCERY_CARD),card_only_replacement(effect))
    def test_controller_and_owner_subjects_keep_their_different_captured_axes(self):
        conditions=GraveyardExileSpec(Scope.OPPONENT_CONTROLLED_CREATURE).conditions('A')
        self.assertTrue(condition_matches(conditions,self.event(owner='A',controller='B')))
        self.assertFalse(condition_matches(conditions,self.event(owner='B',controller='A')))
        self.assertFalse(condition_matches(conditions,self.event(origin='hand',controller=None)))


from quorune.semantic_runtime.graveyard_exile import GraveyardExileReplacementHandler
from quorune.semantic_runtime.zone_replacements import ZoneDestinationReplacementHandler,resolve_zone_change_replacements
from quorune.semantic_runtime.zone_replacement_model import ZoneDestinationReplacementNode
from quorune.composition_zone_replacement_model import COMPONENTS_FIELD,CardZoneComponent
from quorune.replacement.composition_scope import replacement_condition_event


class GraveyardExileRuntimeModelTests(unittest.TestCase):
    event=GraveyardExileModelTests.event
    def effect(self,scope,controller='A'):
        return GraveyardExileReplacementHandler().source_replacement_effect(GraveyardExileSpec(scope).descriptor(),source_ref='source',source_controller=controller,component_id='ability')
    def test_canonical_matcher_and_zone_resolver_agree_on_every_subject_axis(self):
        cases=(
            ('card','battlefield',('creature',),'B','B',(1,1,1,1,0,1)),
            ('noncard','battlefield',('creature',),'B','B',(1,0,1,0,0,1)),
            ('card','stack',('instant',),'B','B',(1,1,0,0,1,0)),
            ('noncard','stack',('instant',),'B','B',(0,0,0,0,0,0)),
            ('card','hand',('creature',),'B',None,(1,1,0,1,0,0)),
            ('card','battlefield',('artifact',),'B','B',(1,1,1,0,0,0)),
            ('card','battlefield',('creature',),'B','A',(1,1,1,1,0,0)),
            ('card','battlefield',('creature',),'A','B',(1,0,1,0,0,1)),
            ('card','battlefield',('enchantment','aura'),'B','B',(1,1,1,0,0,0)),
            ('card','hand',('sorcery',),'A',None,(1,0,0,0,1,0)),
        )
        for kind,origin,types,owner,controller,expected in cases:
            event=self.event(kind,origin,types,owner,controller)
            for scope,applies in zip(Scope,expected):
                with self.subTest(scope=scope,kind=kind,origin=origin,owner=owner,controller=controller):
                    effect=self.effect(scope);self.assertEqual(bool(applies),condition_matches(effect.conditions,event))
                    result=resolve_zone_change_replacements(event_id='event',object_id='object',owner=owner,controller=controller,
                        origin=origin,destination='graveyard',is_card_object=kind=='card',effects=(effect,),apnap_order=('A','B','C','D'),object_types=types)
                    self.assertIsNone(result.pending);self.assertEqual('exile' if applies else 'graveyard',result.destination)
    def test_token_carrier_card_components_only_follow_the_card_only_replacements(self):
        event=self.event(kind='noncard');raw=event.to_dict();raw['payload'][COMPONENTS_FIELD]=[CardZoneComponent('part','part@0','part-ref').to_dict()]
        event=ReplaceableEvent.from_dict(raw)
        for scope,applies in ((Scope.CARD_OR_TOKEN,True),(Scope.OPPONENT_CARD,True),(Scope.PERMANENT,True),(Scope.OPPONENT_CREATURE_CARD,False)):
            effect=self.effect(scope);view=replacement_condition_event(event,effect)
            self.assertEqual(applies,condition_matches(effect.conditions,view))
            self.assertEqual(scope is Scope.OPPONENT_CARD,view is not event)
    def test_source_controller_changes_relative_scope_and_other_destinations_never_match(self):
        event=self.event(owner='B',controller='B')
        for scope in (Scope.OPPONENT_CARD,Scope.OPPONENT_CREATURE_CARD,Scope.OPPONENT_CONTROLLED_CREATURE):
            self.assertTrue(condition_matches(self.effect(scope,'A').conditions,event));self.assertFalse(condition_matches(self.effect(scope,'B').conditions,event))
        for scope in Scope:self.assertFalse(condition_matches(self.effect(scope).conditions,self.event(destination='hand')))
    def test_legacy_counter_handler_and_old_dataclass_wire_fields_are_unchanged(self):
        from quorune.compiler.zone_templates import static_zone_destination_replacement_handler
        text="If a card would be put into an opponent's graveyard from anywhere, instead exile it with a void counter on it."
        compiled=static_zone_destination_replacement_handler(text)
        self.assertEqual('replacement.zone.destination.v1',compiled[1]['handler_id']);self.assertEqual(1,compiled[1]['schema_version'])
        self.assertEqual({'void':1},compiled[1]['counters']);node=ZoneDestinationReplacementHandler().validate(compiled[1])
        self.assertEqual({'destination','object_kind','owner_relation','replacement_destination','counters'},set(asdict(node)))
        self.assertEqual(ZoneDestinationReplacementNode('graveyard','card','opponent','exile',(('void',1),)),node)


from pathlib import Path
import tempfile
from unittest.mock import patch
from dataclasses import replace
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.graveyard_exile_model import GRAVEYARD_EXILE_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database
import test_zone_replacement_compiler as compiler_controls
import test_public_condition_self_replacements as controls

class GraveyardExileCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()
    def test_six_static_subjects_compile_with_current_ability_and_complete_source_spans(self):
        for text in TEXTS:
            ir=compile_oracle_card(compiler_controls._replacement_fixture(text),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0]
            self.assertIn(CAP,node.capability_dependencies);self.assertEqual(HANDLER,node.handlers[0]['handler_id'])
            from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
            self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE,node.runtime_coverage);self.assertEqual(text,node.text)
    def test_replacement_token_recipe_changing_scope_or_tail_and_withdrawal_fail_closed(self):
        from quorune.rules.zone_replacement_token_trigger import CAPABILITY, HANDLER, parse_descriptor

        text = TEXTS[5] + " When you do, create a 2/2 green Wolf creature token."
        recipe = graveyard_exile_template(text)
        self.assertEqual(HANDLER, recipe[1]["handler_id"])
        self.assertEqual(Scope.OPPONENT_CONTROLLED_CREATURE, parse_descriptor(recipe[1])[0].scope)
        self.assertIsNone(graveyard_exile_template(TEXTS[4] + " When you do, create a 2/2 green Wolf creature token."))
        self.assertIsNone(graveyard_exile_template(TEXTS[5] + " When you do, draw a card."))
        self.assertIsNone(graveyard_exile_template(TEXTS[5] + " When you do, create two 2/2 green Wolf creature tokens."))
        fixture = compiler_controls._replacement_fixture(text)
        before = compile_oracle_card(fixture, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", before.status, before.material_residuals)
        with patch("quorune.compiler.graveyard_exile_templates._token_trigger_template", return_value=None):
            after = compile_oracle_card(fixture, capability_registry=self.registry, capability_profile="commander_review")
        self.assertTrue(after.material_residuals)
        self.assertIn(CAPABILITY, before.faces[0].nodes[0].capability_dependencies)

    def test_withdrawing_new_scope_or_its_producer_does_not_widen_legacy_counter_handler(self):
        with patch('quorune.compiler.graveyard_exile_templates.graveyard_exile_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(compiler_controls._replacement_fixture(TEXTS[0]),capability_registry=self.registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(x for x in raw['capabilities'] if x['id']==CAP).update(status='blocked',blockers=['Qualified subject withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual('exact',compile_oracle_card(compiler_controls._replacement_fixture(TEXTS[0]),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(compiler_controls._replacement_fixture(),capability_registry=registry).status)

class GraveyardExileRegistrationTests(unittest.TestCase):
    def test_six_complete_originals_close_normally_while_result_and_sibling_neighbors_remain_unready(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'originals.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,'Kalitas, Traitor of Ghet','Liesa, Forgotten Archangel','Dauthi Voidwalker'):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False);register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name in CANDIDATES,bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])

class GraveyardExileGameplayTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    advance=controls.PublicConditionSelfReplacementTests.advance
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'qualified-exile.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified graveyard exile',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def closed(self,s,card):
        bound=self.binding(s,card);self.assertTrue(bound['strict_capability_ready'],(card.printed_name,bound['blockers']));self.assertEqual('capability_closed',bound['trust_basis'])
    def test_actual_rip_etb_exiles_all_graveyards_then_modified_sacrifice_pays_without_death_trigger(self):
        s=self.start(929010);source=self.add(s.engine,'Rest in Peace',zone='hand');self.closed(s,source)
        graves=[self.add(s.engine,name,seat=seat,zone='graveyard',ref='rip-existing-'+seat) for name,seat in (('Shock','A'),('Grizzly Bears','B'),('Murder','C'))]
        body=self.add(s.engine,'Doomed Traveler');child=self.add(s.engine,'Village Rites',zone='hand');self.closed(s,body);self.closed(s,child)
        secret=self.add(s.engine,'Generic Bound Growth',seat='D',zone='hand',ref='exile-private-D')
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in graves))
        before=len(s.state.players['A'].draw_history);self.priority(s,'A')
        action=next(x for x in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if x['id']=='cast:'+child.ref)
        self.assertIn(body.ref,str(action['cost_options']));command={'action_id':action['id'],'sacrifice_cards':[body.ref],'pay':'auto'}
        self.reject(s,command,'D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('exile',s.state.cards[body.object_id].zone);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+2,len(s.state.players['A'].draw_history));self.assertEqual('exile',s.state.cards[child.object_id].zone)
        for principal in ('pilot:A','pilot:B','pilot:C','spectator'):self.assertNotIn(secret.ref,str(s.packet(principal,full=True)))
        self.assertFalse(any(c.zone=='battlefield' and c.object_kind=='token' for c in s.state.cards.values()));self.replay(s,load=True)

    def test_actual_leyline_exiles_opponent_cards_but_tokens_still_die_and_own_spells_reach_graveyard(self):
        s=self.start(929020);source=self.add(s.engine,'Leyline of the Void',zone='hand');self.closed(s,source)
        body=self.add(s.engine,'Doomed Traveler',seat='B',ref='leyline-opponent-body');self.closed(s,body)
        kill=self.add(s.engine,'Murder',zone='hand');burn=self.add(s.engine,'Shock',zone='hand')
        raise_spell=self.add(s.engine,'Raise the Alarm',seat='B',zone='hand');artist=self.add(s.engine,'Blood Artist',seat='C');self.closed(s,artist)
        s.state.players['B'].mana_pool.update({k:20 for k in 'CWUBRG'});self.ready(s)
        self.cast(s,source);self.assertIsNone(self.resolve(s));self.cast(s,kill,[body.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[body.object_id].zone);self.assertEqual('graveyard',s.state.cards[kill.object_id].zone)
        self.assertFalse(any(c.zone=='battlefield' and c.object_kind=='token' for c in s.state.cards.values()))
        self.assertEqual((40,40),(s.state.players['A'].life,s.state.players['C'].life))
        self.cast(s,raise_spell,seat='B');self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[raise_spell.object_id].zone)
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='B'];self.assertEqual(2,len(tokens))
        self.cast(s,burn,[tokens[0].ref]);decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.target',decision.kind);self.assertEqual(['C'],decision.actors)
        self.assertEqual('outside',s.state.cards[tokens[0].object_id].zone);self.assertEqual('graveyard',s.state.cards[burn.object_id].zone)
        s=self.replay(s,load=True);result=s.act('pilot:C',{'action_id':'choose','targets':['A']});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual((39,41),(s.state.players['A'].life,s.state.players['C'].life));self.replay(s,load=True)
    def test_actual_dryad_hybrid_cast_and_destroy_versus_lethal_damage_keep_source_lifetime_order(self):
        for name in ('Murder','Shock'):
            with self.subTest(removal=name):
                s=self.start(929030+(name=='Shock'));source=self.add(s.engine,'Dryad Militant',zone='hand');removal=self.add(s.engine,name,zone='hand');self.closed(s,source);self.closed(s,removal)
                mana={k:0 for k in 'CWUBRG'}
                if name=='Murder':mana.update(W=1,C=1,B=2)
                else:mana.update(G=1,R=1)
                self.prepare(s,mana=mana);self.checkpoint(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
                self.cast(s,removal,[source.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual('graveyard' if name=='Murder' else 'exile',s.state.cards[removal.object_id].zone)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.replay(s,load=True)

    def test_actual_leyline_opening_action_is_private_and_not_a_spell_cast(self):
        import test_opening_hand_battlefield as opening
        s=opening.OpeningHandBattlefieldRuntimeTests.session(self,929040,{'A':['Leyline of the Void']})
        card=next(s.state.cards[oid] for oid in s.state.players['A'].zones['hand'] if s.state.cards[oid].printed_name=='Leyline of the Void')
        self.closed(s,card);opening.OpeningHandBattlefieldRuntimeTests.keep(self,s)
        self.assertEqual('opening.hand',s.state.pending_decision.kind);self.assertFalse(s.state.started);self.assertFalse(s.state.stack)
        for seat in 'BCD':self.assertNotIn(card.ref,str(s.packet('pilot:'+seat,full=True)))
        command={'action_id':'choose','choices':{'refs':[card.ref]}};self.reject(s,command,'D')
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',s.state.cards[card.object_id].zone);self.assertTrue(s.state.started)
        self.assertEqual((),s.engine._current_turn_history('spell_cast'));self.replay(s,load=True)
    def reach(self,s,kind,seat=None):
        self.advance(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind==kind and (seat is None or game.state.pending_decision.actors==[seat]))
    def test_actual_samurai_bushido_blocks_and_battlefield_exile_does_not_affect_stack_spells(self):
        import test_combat_prevention_source_scopes as combat
        s=self.start(929050);source=self.add(s.engine,'Samurai of the Pale Curtain',zone='hand');self.closed(s,source)
        attacker=self.add(s.engine,'Grizzly Bears',seat='B');burn=self.add(s.engine,'Shock',zone='hand')
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.cast(s,burn,['B']);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[burn.object_id].zone)
        combat.CombatPreventionSourceRuntimeTests.attack(self,s,'B',[(attacker,'A')]);combat.CombatPreventionSourceRuntimeTests.block(self,s,'A',[(source,attacker)])
        self.assertIsNone(self.resolve(s));self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'));self.assertEqual(3,s.engine._numeric_stat(source.object_id,'toughness'))
        s=self.replay(s,load=True);self.advance(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual('exile',s.state.cards[attacker.object_id].zone);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(40,s.state.players['A'].life);self.replay(s,load=True)
    def test_actual_anafenza_attack_targets_another_tapped_controlled_nonattacker(self):
        import test_combat_prevention_source_scopes as combat
        s=self.start(929060);source=self.add(s.engine,'Anafenza, the Foremost',zone='hand');self.closed(s,source)
        recipient=self.add(s.engine,'Grizzly Bears',ref='anafenza-tapped-recipient');recipient.tapped=True
        untapped=self.add(s.engine,'Grizzly Bears',ref='anafenza-untapped');foreign=self.add(s.engine,'Grizzly Bears',seat='C',ref='anafenza-foreign');foreign.tapped=True
        self.add(s.engine,'Fervor');self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        combat.CombatPreventionSourceRuntimeTests.attack(self,s,'A',[(source,'B')]);decision=self.resolve(s)
        self.assertEqual('semantic.target',decision.kind)
        for ref in (source.ref,untapped.ref,foreign.ref):self.reject(s,{'action_id':'choose','targets':[ref]},'A')
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[recipient.object_id].counters.get('+1/+1',0));self.assertFalse(s.state.cards[recipient.object_id].attacking)
        self.replay(s,load=True)
    def test_actual_stone_pays_tap_sacrifice_exiles_target_graveyard_then_draws_or_stops_for_departed_target(self):
        for departed in (False,True):
            with self.subTest(departed=departed):
                s=self.start(929070+departed);source=self.add(s.engine,'Stone of Erech',zone='hand');self.closed(s,source)
                graves=[self.add(s.engine,name,seat='B',zone='graveyard',ref='stone-grave-'+str(i)) for i,name in enumerate(('Shock','Grizzly Bears'))]
                mana={k:0 for k in 'CWUBRG'};mana['C']=3;self.prepare(s,mana=mana);self.checkpoint(s)
                self.cast(s,source);self.assertIsNone(self.resolve(s));self.priority(s,'A')
                action=next(x for x in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if x['id'].startswith('activate:'+source.ref+':'))
                command={'action_id':action['id'],'targets':['B'],'pay':'auto'};self.reject(s,command,'D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                if departed:
                    self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary);self.assertFalse(s.state.players['B'].in_game)
                before=len(s.state.players['A'].draw_history);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(before+(0 if departed else 1),len(s.state.players['A'].draw_history))
                if not departed:self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in graves))
                self.replay(s,load=True)

    def cast_extra(self,s,card,targets=(),seat='A',**extra):
        self.closed(s,card);self.priority(s,seat)
        row=next(x for x in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions'] if x['id']=='cast:'+card.ref)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto',**extra};self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
    def test_actual_owner_and_controller_exile_scopes_diverge_on_borrowed_creatures(self):
        for source_name in ('Anafenza, the Foremost','Stone of Erech'):
            for borrowed_opponent in (False,True):
                with self.subTest(source=source_name,borrowed_opponent=borrowed_opponent):
                    s=self.start(929080+2*(source_name=='Stone of Erech')+borrowed_opponent)
                    source=self.add(s.engine,source_name);self.closed(s,source)
                    owner='B' if borrowed_opponent else 'A';thief='A' if borrowed_opponent else 'B'
                    victim=self.add(s.engine,'Grizzly Bears',seat=owner,ref='scope-borrowed-body')
                    steal=self.add(s.engine,'Act of Treason',seat=thief,zone='hand');kill=self.add(s.engine,'Murder',zone='hand')
                    for seat in 'AB':s.state.players[seat].mana_pool.update({k:30 for k in 'CWUBRG'})
                    self.prepare(s,actor=thief,active=thief,mana={k:30 for k in 'CWUBRG'});self.checkpoint(s)
                    self.cast_extra(s,steal,[victim.ref],seat=thief);self.assertIsNone(self.resolve(s))
                    self.assertEqual((owner,thief),(s.state.cards[victim.object_id].owner,s.state.cards[victim.object_id].controller))
                    self.cast(s,kill,[victim.ref]);self.assertIsNone(self.resolve(s))
                    exile=borrowed_opponent if source_name=='Anafenza, the Foremost' else not borrowed_opponent
                    self.assertEqual('exile' if exile else 'graveyard',s.state.cards[victim.object_id].zone);self.replay(s,load=True)
    def test_actual_anafenza_uses_manifested_and_animated_current_creature_types_before_movement(self):
        for manifest in (False,True):
            with self.subTest(manifest=manifest):
                s=self.start(929090+manifest);source=self.add(s.engine,'Anafenza, the Foremost');self.closed(s,source)
                kill=self.add(s.engine,'Murder',zone='hand')
                if manifest:
                    setup=self.add(s.engine,'Soul Summons',seat='B',zone='hand')
                    victim=self.add(s.engine,'Shock',seat='B',zone='library',ref='manifested-instant-body')
                else:
                    setup=self.add(s.engine,'Lifespark Spellbomb',seat='B')
                    victim=self.add(s.engine,'Forest',seat='B',ref='animated-land-body')
                for seat in 'AB':s.state.players[seat].mana_pool.update({k:30 for k in 'CWUBRG'})
                self.prepare(s,actor='B',active='B',mana={k:30 for k in 'CWUBRG'});self.checkpoint(s)
                self.closed(s,setup)
                if manifest:self.cast_extra(s,setup,seat='B');self.assertIsNone(self.resolve(s));self.assertTrue(s.state.cards[victim.object_id].face_down)
                else:
                    self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                    action=next(x for x in rows if x['id'].startswith('activate:'+setup.ref+':') and 'land' in str(x).casefold())
                    result=s.act('pilot:B',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertIn('creature',s.engine._type_parts(s.engine._effective_card_data(s.state.cards[victim.object_id])['type_line'])[0])
                self.cast(s,kill,[victim.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[victim.object_id].zone)
                self.assertFalse(s.state.cards[victim.object_id].face_down);self.replay(s,load=True)
    def test_actual_bestow_aura_does_not_use_its_printed_creature_type_for_anafenza(self):
        s=self.start(929100);source=self.add(s.engine,'Anafenza, the Foremost');self.closed(s,source)
        body=self.add(s.engine,'Grizzly Bears',seat='B');aura=self.add(s.engine,'Boon Satyr',seat='B',zone='hand')
        destroy=self.add(s.engine,'Naturalize',zone='hand')
        for seat in 'AB':s.state.players[seat].mana_pool.update({k:30 for k in 'CWUBRG'})
        self.prepare(s,actor='B',active='B',mana={k:30 for k in 'CWUBRG'});self.checkpoint(s)
        self.cast_extra(s,aura,[body.ref],seat='B',cost_option='bestow');self.assertIsNone(self.resolve(s))
        self.assertEqual(body.object_id,s.state.cards[aura.object_id].attached_to)
        self.assertNotIn('creature',s.engine._type_parts(s.engine._effective_card_data(s.state.cards[aura.object_id])['type_line'])[0])
        self.cast(s,destroy,[aura.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.replay(s,load=True)
    def test_actual_ability_loss_removes_the_replacement_without_removing_its_creature(self):
        s=self.start(929110);source=self.add(s.engine,'Dryad Militant');self.closed(s,source)
        frog=self.add(s.engine,'Turn to Frog',zone='hand');burn=self.add(s.engine,'Shock',zone='hand')
        self.ready(s);self.cast(s,frog,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[frog.object_id].zone)
        self.cast(s,burn,['B']);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[burn.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)
    def test_actual_static_replacement_follows_its_current_stolen_source_controller(self):
        s=self.start(929120);source=self.add(s.engine,'Anafenza, the Foremost');self.closed(s,source)
        own=self.add(s.engine,'Grizzly Bears',ref='source-control-own');opponent=self.add(s.engine,'Grizzly Bears',seat='B',ref='source-control-other')
        steal=self.add(s.engine,'Act of Treason',seat='B',zone='hand')
        first=self.add(s.engine,'Murder',zone='hand',ref='source-control-kill-first');second=self.add(s.engine,'Murder',zone='hand',ref='source-control-kill-second')
        for seat in 'AB':s.state.players[seat].mana_pool.update({k:30 for k in 'CWUBRG'})
        self.prepare(s,actor='B',active='B',mana={k:30 for k in 'CWUBRG'});self.checkpoint(s)
        self.cast_extra(s,steal,[source.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.cast(s,first,[own.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[own.object_id].zone)
        self.cast(s,second,[opponent.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[opponent.object_id].zone)
        self.replay(s,load=True)

    def test_actual_whole_and_card_only_replacements_preserve_token_carrier_component_scope(self):
        for name in ('Rest in Peace','Leyline of the Void','Anafenza, the Foremost'):
            with self.subTest(source=name):
                s=self.start(929130+CANDIDATES.index(name));source=self.add(s.engine,name,ref='merged-scope-source');self.closed(s,source)
                part=self.add(s.engine,'Vulpikeet',seat='B',zone='hand');self.closed(s,part)
                create=self.add(s.engine,'Raise the Alarm',seat='B',zone='hand');kill=self.add(s.engine,'Murder',zone='hand')
                artist=self.add(s.engine,'Blood Artist',seat='C');self.closed(s,artist)
                for seat in 'AB':s.state.players[seat].mana_pool.update({k:30 for k in 'CWUBRG'})
                self.prepare(s,actor='B',active='B',mana={k:30 for k in 'CWUBRG'});self.checkpoint(s)
                self.cast_extra(s,create,seat='B');self.assertIsNone(self.resolve(s))
                root=next(c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.controller=='B')
                self.priority(s,'B');action=next(x for x in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if x['id']=='cast:'+part.ref)
                price=next(x for x in action['cost_options'] if x['id'].startswith('mutate'))
                result=s.act('pilot:B',{'action_id':action['id'],'cost_option':price['id'],'targets':[root.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.replay(s,load=True)
                result=s.act('pilot:B',{'action_id':'choose','choice':'bottom'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertTrue(s.state.cards[root.object_id].is_token);self.assertEqual(2,len(s.state.cards[root.object_id].permanent_composition.parts))
                self.cast(s,kill,[root.ref]);decision=self.resolve(s)
                if decision is not None and decision.kind=='semantic.choice':
                    chooser='B' if name=='Anafenza, the Foremost' else 'A'
                    ctx=s.packet('pilot:'+chooser,full=True)['decision']['ctx']
                    self.assertEqual('composition_departure_order',ctx['operation'])
                    result=s.act('pilot:'+chooser,{'action_id':'choose','cards':[root.ref,part.ref]});self.assertTrue(result.ok,result.summary);decision=self.resolve(s)
                dies=name!='Rest in Peace'
                if dies:
                    self.assertEqual('semantic.target',decision.kind);self.assertEqual(['C'],decision.actors)
                    result=s.act('pilot:C',{'action_id':'choose','targets':['A']});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                else:self.assertIsNone(decision)
                self.assertEqual('graveyard' if name=='Anafenza, the Foremost' else 'exile',s.state.cards[part.object_id].zone)
                self.assertEqual('outside',s.state.cards[root.object_id].zone)
                self.assertEqual((39,41) if dies else (40,40),(s.state.players['A'].life,s.state.players['C'].life));self.replay(s,load=True)
    def test_actual_source_self_and_simultaneous_departure_use_pre_move_replacements(self):
        for name in ('Rest in Peace','Samurai of the Pale Curtain'):
            with self.subTest(source=name):
                s=self.start(929140+(name!='Rest in Peace'));source=self.add(s.engine,name,ref='00-exile-source');self.closed(s,source)
                own=self.add(s.engine,'Grizzly Bears',ref='01-exile-own');foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='02-exile-foreign')
                wipe=self.add(s.engine,"Akroma's Vengeance",zone='hand');self.closed(s,wipe)
                self.ready(s);self.cast(s,wipe);self.assertIsNone(self.resolve(s))
                self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in (source,own,foreign)))
                self.assertEqual('graveyard',s.state.cards[wipe.object_id].zone);self.replay(s,load=True)
    def test_applied_replacement_creates_token_after_source_and_victim_leave_together(self):
        s = self.start(929181)
        source = self.add(s.engine, "Head of the Hunt")
        own = self.add(s.engine, "Grizzly Bears", ref="token-replacement-own")
        victim = self.add(s.engine, "Doomed Traveler", seat="B", ref="token-replacement-opponent")
        wipe = self.add(s.engine, "Akroma's Vengeance", zone="hand")
        self.closed(s, source)
        self.ready(s)
        self.cast(s, wipe)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(["graveyard", "graveyard", "exile"], [
            s.state.cards[card.object_id].zone for card in (source, own, victim)
        ])
        wolves = [card for card in s.state.cards.values() if card.is_token and card.zone=="battlefield"]
        self.assertEqual([("Wolf Token", "A")], [(card.printed_name, card.controller) for card in wolves])
        self.replay(s, load=True)

    def test_competing_replacements_create_token_only_for_the_committed_selected_source(self):
        from quorune.rules.zone_replacement_token_trigger import HANDLER as token_handler

        for selected in (False, True):
            with self.subTest(selected=selected):
                s = self.start(929182+int(selected))
                source = self.add(s.engine, "Head of the Hunt")
                other = self.add(s.engine, "Rest in Peace")
                victim = self.add(s.engine, "Grizzly Bears", seat="B", ref="token-replacement-contested")
                removal = self.add(s.engine, "Murder", zone="hand")
                self.closed(s, source)
                self.closed(s, other)
                self.ready(s)
                self.cast(s, removal, [victim.ref])
                self.assertEqual("replacement.order", self.resolve(s).kind)
                self.assertEqual(["pilot:B"], s.pending_principals())
                s = self.replay(s, load=True)
                rows = s.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                choice = next(row["id"] for row in rows if row["id"].startswith(token_handler) is selected)
                result = s.act("pilot:B", {"action_id":"choose","replacement":choice})
                self.assertTrue(result.ok, result.summary)
                next_decision = self.resolve(s)
                if next_decision is not None and next_decision.kind=="replacement.order":
                    proposed = s.packet(s.pending_principals()[0], full=True)["decision"]["ctx"]["options"]
                    result = s.act(s.pending_principals()[0], {"action_id":"choose","replacement":proposed[0]["id"]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(s))
                self.assertEqual("exile", s.state.cards[victim.object_id].zone)
                wolves = [card for card in s.state.cards.values()
                          if card.is_token and card.zone=="battlefield" and card.controller=="A"]
                self.assertEqual(int(selected), len(wolves))
                self.replay(s, load=True)

    def test_actual_competing_exile_sources_choose_once_and_modified_sacrifice_is_paid(self):
        s=self.start(929150);rip=self.add(s.engine,'Rest in Peace');leyline=self.add(s.engine,'Leyline of the Void',seat='B')
        self.closed(s,rip);self.closed(s,leyline)
        body=self.add(s.engine,'Grizzly Bears');spell=self.add(s.engine,'Village Rites',zone='hand')
        self.ready(s);self.priority(s,'A')
        action=next(x for x in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if x['id']=='cast:'+spell.ref)
        command={'action_id':action['id'],'sacrifice_cards':[body.ref],'pay':'auto'};self.reject(s,command,'D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertEqual('replacement.order',s.state.pending_decision.kind)
        self.assertEqual('battlefield',s.state.cards[body.object_id].zone);self.assertEqual('hand',s.state.cards[spell.object_id].zone)
        s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertEqual(2,len(options));choice=options[0]['id'];result=s.act('pilot:A',{'action_id':'choose','replacement':choice});self.assertTrue(result.ok,result.summary)
        self.assertEqual('exile',s.state.cards[body.object_id].zone);self.assertEqual('stack',s.state.cards[spell.object_id].zone)
        before=len(s.state.players['A'].draw_history);decision=self.resolve(s)
        self.assertEqual('replacement.order',decision.kind);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        result=s.act('pilot:A',{'action_id':'choose','replacement':options[0]['id']});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+2,len(s.state.players['A'].draw_history));self.assertEqual('exile',s.state.cards[spell.object_id].zone)
        self.assertEqual(1,sum(e.code=='cost.sacrifice' and e.details.get('object')==body.ref for e in s.state.events));self.replay(s,load=True)
    def test_initial_phased_source_stays_inactive_until_its_real_untap_phase_in(self):
        from quorune.phasing_model import PhasingRecord
        s=self.start(929160);source=self.add(s.engine,'Anafenza, the Foremost');self.closed(s,source)
        source.phased_out=True;source.phasing_record=PhasingRecord('A')
        first=self.add(s.engine,'Grizzly Bears',seat='B',ref='phase-before-body');second=self.add(s.engine,'Grizzly Bears',seat='B',ref='phase-after-body')
        removal1=self.add(s.engine,'Murder',zone='hand',ref='phase-before-removal');removal2=self.add(s.engine,'Murder',zone='hand',ref='phase-after-removal')
        for i in range(3):self.add(s.engine,'Swamp',ref='phase-real-mana-'+str(i))
        self.ready(s);self.cast(s,removal1,[first.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
        self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.advance(s,lambda game:game.state.active_player=='A' and game.state.phase=='precombat_main')
        self.assertFalse(s.state.cards[source.object_id].phased_out);self.cast(s,removal2,[second.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[second.object_id].zone);self.replay(s,load=True)
    def test_actual_replaced_commander_death_uses_exile_then_owner_state_action(self):
        self.deck=DeckDefinition('Exile commander',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])
        for redirect in (False,True):
            with self.subTest(redirect=redirect):
                s=self.start(929170+redirect);source=self.add(s.engine,'Rest in Peace');self.closed(s,source)
                commander=next(c for c in s.state.cards.values() if c.owner=='A' and c.is_commander)
                removal=self.add(s.engine,'Murder',zone='hand')
                self.ready(s);self.cast(s,commander);self.assertIsNone(self.resolve(s));self.cast(s,removal,[commander.ref]);decision=self.resolve(s)
                self.assertEqual('state.commander_zone',decision.kind);self.assertEqual('exile',s.state.cards[commander.object_id].zone)
                self.assertEqual('exile',s.state.cards[removal.object_id].zone);s=self.replay(s,load=True)
                choice='command' if redirect else 'remain';command={'action_id':choice,'choice':choice};self.reject(s,command,'D')
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('command' if redirect else 'exile',s.state.cards[commander.object_id].zone)
                self.assertEqual(1,s.state.players['A'].commander_casts[commander.oracle_id]);self.replay(s,load=True)
    def test_fault_after_exile_commit_rolls_back_entire_resolution_and_retries_once(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.start(929180);source=self.add(s.engine,'Rest in Peace');self.closed(s,source)
        body=self.add(s.engine,'Grizzly Bears',seat='B');removal=self.add(s.engine,'Murder',zone='hand')
        self.ready(s);self.cast(s,removal,[body.ref]);original=ZoneTransitionOwner._commit_move;partial=[];failed=None
        def fault(owner,plan,departure,**kwargs):
            value=original(owner,plan,departure,**kwargs)
            if plan.card.object_id==body.object_id:
                partial.append(plan.card.zone);raise GameRuleError('Injected qualified-exile after movement fault')
            return value
        with patch.object(ZoneTransitionOwner,'_commit_move',fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual(['exile'],partial);self.assertIsNotNone(failed);self.assertEqual('battlefield',s.state.cards[body.object_id].zone)
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[body.object_id].zone);self.replay(s,load=True)
    def test_fresh_controller_for_owner_scope_substitution_is_killed_by_actual_borrowed_creature(self):
        from contextlib import nullcontext
        original=GraveyardExileSpec.conditions
        def mutant(spec,controller):
            conditions=original(spec,controller)
            if spec.scope is Scope.OPPONENT_CREATURE_CARD:conditions.pop('owner');conditions['chooser']={'not_in':[controller]}
            return conditions
        with patch.object(GraveyardExileSpec,'conditions',mutant),patch.object(self,'subTest',lambda **kwargs:nullcontext()):
            with self.assertRaises(AssertionError):self.test_actual_owner_and_controller_exile_scopes_diverge_on_borrowed_creatures()
    def test_new_handler_requires_its_fine_scope_even_without_coverage(self):
        s=self.start(929190);source=self.add(s.engine,'Rest in Peace');self.closed(s,source)
        p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(h.get('handler_id')==HANDLER for h in p.handlers))
        deps=tuple(c for c in p.capability_dependencies if c!=CAP)
        missing=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])
