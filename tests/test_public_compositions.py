"""Existing attachment and public movement owners retain their exact meanings."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_source_attack_alone as native
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

GRANT='continuous.attached.fixed_characteristics'
MOVE='zone.move.fixed_public_set'


class PublicCompositionCompilerTests(unittest.TestCase):
    def test_general_attached_objects_keep_exact_predicates_and_unknown_children_residual(self):
        from quorune.compiler.continuous_templates import attached_quoted_ability_handler
        registry=load_default_capability_registry()
        for kind,types in (('artifact',['artifact']),('battle',['battle']),('enchantment',['enchantment']),('permanent',[]),('planeswalker',['planeswalker'])):
            text='Enchanted '+kind+' has "At the beginning of your upkeep, you lose 1 life."'
            ir=compile_oracle_card(replace(query_record(text),type_line='Enchantment — Aura'),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals);outer=ir.faces[0].nodes[0]
            self.assertEqual(types,outer.handlers[0]['condition']['types_all']);self.assertEqual(2,len(ir.faces[0].nodes))
        for text in ('Enchanted player has "At the beginning of your upkeep, you lose 1 life."',
                'Enchanted permanent has "Rewrite history."',
                'Enchanted planeswalker has "[+1]: Draw a card."',
                'Enchanted permanent has "Discard two artifact cards: Untap this permanent."',
                'Enchanted artifact has "At the beginning of your upkeep, you lose 1 life." Draw a card.'):
            ir=compile_oracle_card(query_record(text),capability_registry=registry);self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_new_graveyard_hand_queries_are_unions_and_card_only_without_new_battlefield_forms(self):
        from quorune.compiler.public_zone_move_templates import public_zone_move_effect_template
        from quorune.public_zone_moves import PublicZoneMoveSetSpec,select_public_zone_move_objects
        from quorune.object_query import ObjectQueryResult
        def row(ref,types,*,token=False,owner='A'):
            return ObjectQueryResult(object_id=ref,ref=ref,logical_object_id=ref+':1',printed_name=ref,owner=owner,controller=owner,zone='graveyard',types=types,token=token)
        rows=(row('instant',('instant',)),row('sorcery',('sorcery',)),row('creature',('creature',)),row('land',('land',)),row('token',('creature',),token=True),row('foreign',('instant',),owner='B'))
        for subject,expected in (('instant and sorcery ',{'instant','sorcery'}),('',{'instant','sorcery','creature','land'}),('permanent ',{'creature','land'})):
            compiled=public_zone_move_effect_template('Return all '+subject+'cards from your graveyard to your hand.')
            self.assertIsNotNone(compiled);spec=compiled.spec
            self.assertFalse(spec.query.token);actual=select_public_zone_move_objects(rows,spec,actor='A',active_seats='ABCD',apnap_order='ABCD')
            self.assertEqual(expected,{r.ref for r in actual})
        for text in ('Return all instant cards from your graveyard to the battlefield.',
                'Put all instant and sorcery cards from your graveyard onto the battlefield.',
                'Return all cards from your graveyard to the battlefield.',
                'Return all basic permanent cards from your graveyard to your hand.',
                'Return all creature cards from your graveyard to your hand that died this turn.',
                'Return all unrepresented cards from your graveyard to your hand.'):
            self.assertIsNone(public_zone_move_effect_template(text))

    def test_cost_choice_preflight_reuses_the_closed_model_and_rejects_wider_counts(self):
        from quorune.granted_activation_model import normalize_granted_cost_choices,GrantedActivatedAbilitySpec
        single={'kind':'discard','count':1,'zone':'hand','card_type':'artifact','another':False}
        self.assertEqual(single,dict(normalize_granted_cost_choices((single,))[0]))
        for changes in ({'count':2},{'kind':'exile'},{'another':True},{'card_type':'instant'},{'zone':'graveyard'}):
            with self.assertRaises(ValueError):normalize_granted_cost_choices(({**single,**changes},))
        old=GrantedActivatedAbilitySpec('id','key','{T}','Draw a card.',tap_source=True)
        self.assertEqual(old,GrantedActivatedAbilitySpec.from_dict(old.to_dict()))

    def test_instant_sorcery_intersection_mutant_is_killed(self):
        import quorune.compiler.public_zone_move_templates as owner
        from quorune.object_query import ObjectQueryResult,query_objects
        original=owner._graveyard_card_query
        rows=tuple(ObjectQueryResult(object_id=kind,ref=kind,printed_name=kind,owner='A',controller='A',zone='graveyard',types=(kind,)) for kind in ('instant','sorcery'))
        def witness():
            spec=owner.public_zone_move_effect_template('Return all instant and sorcery cards from your graveyard to your hand.').spec
            self.assertEqual({'instant','sorcery'},{r.ref for r in query_objects(rows,spec.query)})
        witness()
        def intersection(text):
            query=original(text)
            return replace(query,types_all=query.types_any,types_any=()) if query is not None and query.types_any==('instant','sorcery') else query
        with patch.object(owner,'_graveyard_card_query',side_effect=intersection),self.assertRaises(AssertionError):witness()

    def test_verified_owner_withdrawals_preserve_unrelated_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());d=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap,text in ((GRANT,'Enchanted artifact has "At the beginning of your upkeep, you lose 2 life."'),(MOVE,'Return all instant and sorcery cards from your graveyard to your hand.')):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['public composition owner withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=d,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)


class PublicCompositionNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.SourceAttackAloneNativeTests.session
    add=native.SourceAttackAloneNativeTests.add
    prepare=native.SourceAttackAloneNativeTests.prepare
    cast=native.SourceAttackAloneNativeTests.cast
    cast_for=native.SourceAttackAloneNativeTests.cast_for
    action=native.SourceAttackAloneNativeTests.action
    priority=native.SourceAttackAloneNativeTests.priority
    checkpoint=native.SourceAttackAloneNativeTests.checkpoint
    resolve=native.SourceAttackAloneNativeTests.resolve
    replay=native.SourceAttackAloneNativeTests.replay
    assert_closed=native.SourceAttackAloneNativeTests.assert_closed
    until=native.SourceAttackAloneNativeTests.until
    resolve_top=native.SourceAttackAloneNativeTests.resolve_top

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'public-compositions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/public-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public compositions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def next_upkeep(self,session,seat):
        for _ in range(30):
            session.engine.pump()
            if session.state.active_player==seat and session.state.step=='upkeep' and session.state.stack:return
            decision=session.state.pending_decision;principal=session.pending_principals()[0]
            if decision.kind=='cleanup.discard':
                actor=decision.actors[0];ctx=decision.payload_by_actor[actor]
                command={'a':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
            else:self.assertEqual('priority',decision.kind);command={'action_id':'pass'}
            result=session.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('The recipient upkeep trigger did not occur')

    def test_actual_relic_bane_grants_upkeep_loss_to_artifact_controller_after_aura_departure(self):
        session=self.session(303640001);source=self.add(session.engine,'Relic Bane');recipient=self.add(session.engine,'Sol Ring',seat='B')
        destroy=self.add(session.engine,'Disenchant',zone='hand');self.add(session.engine,'Plains');self.add(session.engine,'Wastes');self.assert_closed(session,source)
        attach_objects(session.state.cards,source,recipient,source_timestamp=source.zone_timestamp,players=session.state.players)
        self.prepare(session);session.state.phase_index=TURN_STEPS.index(('ending','end_step'));session.state.phase='ending';session.state.step='end_step';self.checkpoint(session)
        life={s:session.state.players[s].life for s in 'ABCD'};private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref;self.next_upkeep(session,'B')
        self.assertEqual('B',session.state.stack[-1].controller);self.assertEqual(recipient.object_id,session.state.stack[-1].source_object_id)
        self.cast(session,'A',destroy,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(life['B']-2,session.state.players['B'].life)
        for seat in 'ACD':self.assertEqual(life[seat],session.state.players[seat].life)
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_clawing_torment_grants_upkeep_loss_to_noncreature_controller(self):
        session=self.session(303640010);source=self.add(session.engine,'Clawing Torment',zone='hand');body=self.add(session.engine,'Sol Ring',seat='B')
        self.assert_closed(session,source);self.prepare(session,mana={'B':1});self.checkpoint(session);self.cast(session,'A',source,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id,session.state.cards[source.object_id].attached_to)
        expected=session.state.cards[session.state.players['B'].zones['library'][-1]].ref
        self.until(session,lambda s:s.state.active_player=='B' and s.state.step=='upkeep' and bool(s.state.stack))
        life={s:session.state.players[s].life for s in 'AB'};session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(life['B']-1,session.state.players['B'].life);self.assertEqual(life['A'],session.state.players['A'].life)
        self.until(session,lambda s:s.state.active_player=='B' and s.state.step=='main')
        self.assertIn(expected,json.dumps(session.packet('pilot:B',full=True)))
        for seat in 'ACD':self.assertNotIn(expected,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_summonings_revalidates_artifact_restriction_then_returns_current_instant_sorcery_union(self):
        session=self.session(303640020);source=self.add(session.engine,'Metallurgic Summonings')
        for i in range(5):self.add(session.engine,'Ornithopter',ref='public-artifact-'+str(i))
        sixth=self.add(session.engine,'Memnite',zone='hand');instant=self.add(session.engine,'Giant Growth',zone='graveyard');sorcery=self.add(session.engine,'Divination',zone='graveyard')
        creature=self.add(session.engine,'Grizzly Bears',zone='graveyard');land=self.add(session.engine,'Forest',zone='graveyard');foreign=self.add(session.engine,'Counterspell',seat='C',zone='graveyard')
        counter=self.add(session.engine,'Counterspell',zone='hand',ref='own-public-counter');spell=self.add(session.engine,'Disenchant',seat='B',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'U':4});session.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(session);self.priority(session,'A')
        prefix='activate:'+source.ref+':'
        self.assertFalse(any(a['id'].startswith(prefix) for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.cast_for(session,'A',sixth,reserve={'C':3,'U':4});self.assertIsNone(self.resolve(session));self.priority(session,'A')
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith(prefix))
        before=authoritative_state_hash(session.state);command={'action_id':offered['id'],'pay':'auto','reserve':{'U':2}}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.cast(session,'B',spell,[sixth.ref]);stack_spell=next(i for i in session.state.stack if i.card_object_id==spell.object_id);self.cast(session,'A',counter,[stack_spell.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (instant,sorcery,counter):self.assertEqual('hand',session.state.cards[card.object_id].zone)
        for card in (creature,land,foreign):self.assertEqual('graveyard',session.state.cards[card.object_id].zone)
        self.assertEqual('exile',session.state.cards[source.object_id].zone);self.replay(session,load=True)
