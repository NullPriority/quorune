"""Pinned CR508/603: qualified declaration counts and recipient grouping."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_qualified_attack_subjects as native
from quorune.attack_batch_model import (QualifiedAttackBatchSpec,ATTACK_BATCH_CONDITION,
    ATTACK_BATCH_CONTEXT,QUALIFIED_ATTACK_BATCH_CAPABILITY as CAPABILITY)
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.attack_batches import qualified_attack_match_count,attack_batch_aggregation_scope
from quorune.rules.qualified_attacks import attack_characteristic_context
from quorune.object_query import ObjectQueryResult
from scripts.build_test_database import build_fixture_database


class QualifiedAttackBatchCompilerTests(unittest.TestCase):
    def test_batch_subjects_and_explicit_grouping_are_closed_and_versioned(self):
        registry=load_default_capability_registry()
        for head,minimum,group in (
            ('Whenever you attack with one or more Lizards',1,'declaration'),
            ('Whenever one or more nontoken Vampires you control attack',1,'declaration'),
            ('Whenever you attack with two or more creatures with flying',2,'declaration'),
            ('Whenever you attack with one or more creatures with counters on them',1,'declaration'),
            ('Whenever you attack with five or more Soldiers',5,'declaration'),
            ('Whenever you attack with one or more creatures with power 4 or greater',1,'declaration'),
            ('Whenever one or more Halflings you control attack a player',1,'player'),
            ('Whenever two or more creatures you control attack a player',2,'player'),
            ('Whenever you attack a player or planeswalker with one or more creatures with power 1 or less',1,'player_or_planeswalker')):
            text=head+', draw a card.';ir=compile_oracle_card(query_record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,(text,ir.material_residuals))
            node=ir.faces[0].nodes[0];self.assertIn('one_or_more_event_batch',node.runtime_coverage);self.assertIn(CAPABILITY,node.capability_dependencies)
            from quorune.attack_batch_model import attack_batch_conditions
            spec=attack_batch_conditions(node.event_condition)[0]
            self.assertEqual((minimum,group),(spec.minimum,spec.grouping));self.assertEqual(spec,QualifiedAttackBatchSpec.from_dict(spec.to_dict()))
        for text in ('Whenever you attack with X or more creatures, draw a card.',
                'Whenever one or more chosen creatures you control attack, draw a card.',
                'Whenever one or more Halflings you control attack a battle, draw a card.',
                'Whenever one or more Dragons you control attack, it gets +1/+1 until end of turn.',
                'Whenever two or more creatures you control attack a player, rewrite history.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def row(self,ref,recipient='B',kind='player',*,flying=True,counters=None,power=1,subtypes=('bird',)):
        public=ObjectQueryResult(ref,ref,'Attacker','A','A','battlefield',types=('creature',),subtypes=subtypes,
            keywords=('flying',) if flying else (),counters=counters or {},effective_power=power,effective_toughness=2,attacking=True,tapped=True)
        host=SimpleNamespace(state=SimpleNamespace(qualified_attack_facts_version=1),_public_object_query_result=lambda card:public)
        return {'reference':ref,'logical_object_id':ref+'@1','controller':'A','recipient_kind':kind,'recipient':recipient,
            'recipient_logical_object_id':None if kind=='player' else recipient+'@1',
            'defending_player':'B' if kind!='player' else recipient,'characteristics':attack_characteristic_context(host,None)}

    def condition(self,query,minimum=1,grouping='declaration'):
        spec=QualifiedAttackBatchSpec(query,minimum,grouping)
        return {'field':ATTACK_BATCH_CONDITION,'op':'gte','value':minimum,'attack_batch':spec.to_dict()}

    def test_matching_minimum_and_recipient_matrix_use_sealed_values_without_mutation(self):
        source=SimpleNamespace(ref='observer',controller='A');query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('flying',))
        rows=[self.row('a'),self.row('b','C'),self.row('c','walker-1','planeswalker'),self.row('d','walker-2','planeswalker'),self.row('e','battle-1','battle'),self.row('f',flying=False)]
        context={'card':'a','defender':'B','attacked_recipient_kind':'player',ATTACK_BATCH_CONTEXT:rows};before=deepcopy(context)
        self.assertEqual(5,qualified_attack_match_count(source,self.condition(query,2),context));self.assertEqual(before,context)
        self.assertEqual(1,qualified_attack_match_count(source,self.condition(query,2,'player'),context))
        for ref,target,kind in (('b','C','player'),('c','walker-1','planeswalker'),('d','walker-2','planeswalker')):
            current={**context,'card':ref,'defender':target,'attacked_recipient_kind':kind}
            condition=self.condition(query,1,'player_or_planeswalker')
            self.assertEqual(1,qualified_attack_match_count(source,condition,current))
            program=SimpleNamespace(event_condition=condition)
            self.assertEqual(target,attack_batch_aggregation_scope(program,current)['attacked_recipient'])
        battle={**context,'card':'e','defender':'battle-1','attacked_recipient_kind':'battle'}
        self.assertEqual(0,qualified_attack_match_count(source,self.condition(query,1,'player_or_planeswalker'),battle))
        nonmatching={**context,'card':'f'}
        self.assertEqual(0,qualified_attack_match_count(source,self.condition(query,2),nonmatching))
        for change in ({'minimum':True},{'schema_version':True},{'grouping':'defending_controller'},{'exclude_source':'yes'}):
            with self.assertRaises(ValueError):QualifiedAttackBatchSpec.from_dict({**QualifiedAttackBatchSpec(query).to_dict(),**change})
        for altered in ({**context,ATTACK_BATCH_CONTEXT:rows+[rows[0]]},{**context,ATTACK_BATCH_CONTEXT:[]},
                {**context,'defender':'C'}):
            with self.assertRaises(ValueError):qualified_attack_match_count(source,self.condition(query,1,'player'),altered)

    def test_snapshot_codec_rejects_contradictory_facts_and_counts_total_counters(self):
        from quorune.object_predicate import PermanentStatePredicateSpec
        source=SimpleNamespace(ref='observer',controller='A')
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),state_predicate=PermanentStatePredicateSpec(minimum_total_counter_count=3))
        row=self.row('a',counters={'charge':4});context={'card':'a','defender':'B','attacked_recipient_kind':'player',ATTACK_BATCH_CONTEXT:[row]}
        self.assertEqual(1,qualified_attack_match_count(source,self.condition(query),context))
        for mutate in (lambda r:r['characteristics'].update(attacker_characteristics_version=True),
                lambda r:r['characteristics'].update(attacker_characteristics_token=1),
                lambda r:r['characteristics'].update(attacker_characteristics_total_counters=0),
                lambda r:r.update(defending_player='C'),lambda r:r.update(extra=None)):
            altered=deepcopy(context);mutate(altered[ATTACK_BATCH_CONTEXT][0])
            with self.assertRaises(ValueError):qualified_attack_match_count(source,self.condition(query),altered)
        from quorune.attack_batch_model import attack_batch_conditions
        condition=self.condition(query)
        with self.assertRaises(ValueError):attack_batch_conditions({**condition,'value':True})
        with self.assertRaises(ValueError):attack_batch_conditions({**condition,'value':2})

    def test_declaration_collection_is_immutable_and_legacy_context_stays_omitted(self):
        from quorune.rules.attack_batches import seal_attack_declaration
        from quorune.rules.qualified_attacks import attack_characteristic_context
        from quorune.attack_transition_model import AttackRecipientKind
        participant=SimpleNamespace(object_id='a',reference='a',logical_object_id='a@1',controller='A')
        event=SimpleNamespace(participants=(participant,),assignments=(SimpleNamespace(attacker_object_id='a',
            recipient=SimpleNamespace(kind=AttackRecipientKind.PLAYER,reference='B',defending_player='B',logical_object_id=None)),))
        facts=self.row('a')['characteristics'];sealed=seal_attack_declaration(event,{'a':facts})
        facts['attacker_characteristics_keywords'].clear()
        self.assertEqual(('flying',),sealed[0]['characteristics']['attacker_characteristics_keywords'])
        with self.assertRaises(TypeError):sealed[0]['controller']='B'
        self.assertEqual({},attack_characteristic_context(SimpleNamespace(state=SimpleNamespace(qualified_attack_facts_version=None)),None))

    def test_verified_batch_owner_withdrawal_keeps_singular_attack_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['attack batch owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(query_record('Whenever you attack with two or more creatures with flying, draw a card.'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Whenever a Dragon you control attacks, draw a card.'),capability_registry=registry).status)

    def test_total_attacker_proxy_mutant_is_killed_by_a_nonmatching_second_creature(self):
        source=SimpleNamespace(ref='observer',controller='A');query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('flying',))
        context={'card':'a','defender':'B','attacked_recipient_kind':'player',ATTACK_BATCH_CONTEXT:[self.row('a'),self.row('b',flying=False)]}
        import quorune.rules.attack_batches as owner
        def witness():self.assertLess(owner.qualified_attack_match_count(source,self.condition(query,2),context),2)
        witness()
        with patch.object(owner,'qualified_attack_match_count',return_value=2),self.assertRaises(AssertionError):witness()


class QualifiedAttackBatchNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.QualifiedAttackNativeTests.session
    add=native.QualifiedAttackNativeTests.add
    prepare=native.QualifiedAttackNativeTests.prepare
    cast=native.QualifiedAttackNativeTests.cast
    cast_for=native.QualifiedAttackNativeTests.cast_for
    action=native.QualifiedAttackNativeTests.action
    priority=native.QualifiedAttackNativeTests.priority
    checkpoint=native.QualifiedAttackNativeTests.checkpoint
    resolve=native.QualifiedAttackNativeTests.resolve
    replay=native.QualifiedAttackNativeTests.replay
    assert_closed=native.QualifiedAttackNativeTests.assert_closed
    until=native.QualifiedAttackNativeTests.until
    attack=native.QualifiedAttackNativeTests.attack
    trigger_count=native.QualifiedAttackNativeTests.trigger_count
    activate=native.QualifiedAttackNativeTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-attack-batches.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-attack-batch-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attack batches',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_runtime_batch_capability_and_coverage_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(508631001);source=self.add(session.engine,'Tide Skimmer');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            self.assertFalse(bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')['strict'])
            changed=replace(program,coverage=tuple(c for c in program.coverage if c!='one_or_more_event_batch'))
            self.assertFalse(bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')['strict'])
        self.assertGreater(checked,0)

    def test_actual_mavren_fires_once_across_defenders_and_queued_token_survives_giver_departure(self):
        session=self.session(508631010);source=self.add(session.engine,'Mavren Fein, Dusk Apostle',zone='hand')
        first=self.add(session.engine,'Universal Automaton',ref='nontoken-vampire-one');second=self.add(session.engine,'Universal Automaton',ref='nontoken-vampire-two')
        token=self.add(session.engine,'Universal Automaton',ref='token-vampire');token.is_token=True;token.object_kind='token'
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        for card in (first,second,token):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(first,'B'),(second,'C'),(token,'D')]);self.assertEqual(1,self.trigger_count(session,source))
        self.cast(session,'B',removal,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        created=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token and c.object_id!=token.object_id]
        self.assertEqual(1,len(created));self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertIn('lifelink',session.engine._combat_keywords(created[0]));self.assertNotIn(created[0].object_id,session.state.combat.attackers)
        self.replay(session,load=True)

    def test_actual_mavren_token_only_declaration_does_not_qualify(self):
        session=self.session(508631020);source=self.add(session.engine,'Mavren Fein, Dusk Apostle',zone='hand')
        token=self.add(session.engine,'Universal Automaton');token.is_token=True;token.object_kind='token';token.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.attack(session,[(token,'B')])
        self.assertEqual(0,self.trigger_count(session,source));self.assertEqual(1,sum(c.is_token and c.zone=='battlefield' for c in session.state.cards.values()))
        self.replay(session,load=True)

    def test_actual_tide_minimum_counts_fliers_and_keeps_queued_draw_after_they_leave(self):
        for qualifies in (False,True):
            session=self.session(508631030+int(qualifies));source=self.add(session.engine,'Tide Skimmer',zone='hand')
            first=self.add(session.engine,'Wind Drake');second=self.add(session.engine,'Wind Drake' if qualifies else 'Grizzly Bears',ref='second-tide-attacker')
            removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
            for card in (first,second):card.acquired_control_turn_count=-1
            self.assert_closed(session,source);self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
            self.cast(session,'A',source);self.assertIsNone(self.resolve(session));hand=len(session.state.players['A'].zones['hand'])
            self.attack(session,[(first,'B'),(second,'C')]);self.assertEqual(int(qualifies),self.trigger_count(session,source))
            if qualifies:
                self.cast(session,'B',removal,[first.ref]);session=self.replay(session,load=True)
                self.assertIsNone(self.resolve(session));self.assertEqual(hand+2,len(session.state.players['A'].zones['hand']))
                drawn=session.state.cards[session.state.players['A'].zones['hand'][-1]].ref
                session=self.replay(session,load=True)
                for seat in 'BCD':self.assertNotIn(drawn,json.dumps(session.packet('pilot:'+seat,full=True)))
            else:self.replay(session,load=True)

    def test_actual_meriadoc_groups_by_player_and_excludes_a_planeswalker_recipient(self):
        session=self.session(508631040);source=self.add(session.engine,'Meriadoc Brandybuck',zone='hand')
        bodies=[self.add(session.engine,'Universal Automaton',ref='halfling-'+str(i)) for i in range(5)]
        walker=self.add(session.engine,'Jace Beleren',seat='B');walker.counters['loyalty']=3
        battle=self.add(session.engine,'Invasion of Zendikar');battle.battle_protector='B';battle.counters['defense']=3
        for card in bodies:card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'G':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(bodies[0],'B'),(bodies[1],'B'),(bodies[2],'C'),(bodies[3],walker.ref),(bodies[4],battle.ref)])
        self.assertEqual(2,self.trigger_count(session,source));session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(2,len(tokens))
        for card in tokens:self.assertIn('food',session.engine._type_parts(session.engine._effective_card_data(card)['type_line'])[1])
        self.replay(session,load=True)

    def test_actual_rigo_keeps_two_planeswalkers_and_their_controller_as_three_recipients(self):
        session=self.session(508631050);source=self.add(session.engine,'Rigo, Streetwise Mentor',zone='hand')
        bodies=[self.add(session.engine,'Memnite',ref='rigo-small-'+str(i)) for i in range(4)]
        big=self.add(session.engine,'Grizzly Bears');first=self.add(session.engine,'Jace Beleren',seat='B');second=self.add(session.engine,'Ajani Goldmane',seat='B')
        first.counters['loyalty']=3;second.counters['loyalty']=4
        for card in (*bodies,big):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'W':3});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));hand=len(session.state.players['A'].zones['hand'])
        self.attack(session,[(bodies[0],'B'),(bodies[1],first.ref),(bodies[2],first.ref),(bodies[3],second.ref),(big,'C')])
        self.assertEqual(3,self.trigger_count(session,source));session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+3,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_actual_landroval_minimum_is_per_player_and_planeswalker_attack_does_not_complete_it(self):
        for qualifies in (False,True):
            session=self.session(508631060+int(qualifies));source=self.add(session.engine,'Landroval, Horizon Witness',zone='hand')
            bodies=[self.add(session.engine,'Grizzly Bears',ref='landroval-attacker-'+str(i)) for i in range(3)]
            walker=self.add(session.engine,'Jace Beleren',seat='B');walker.counters['loyalty']=3
            for card in bodies:card.acquired_control_turn_count=-1
            self.assert_closed(session,source);self.prepare(session,mana={'C':4,'W':1});self.checkpoint(session)
            self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
            self.attack(session,[(bodies[0],'B'),(bodies[1],'B' if qualifies else 'C'),(bodies[2],walker.ref)])
            if qualifies:
                self.assertIsNotNone(self.resolve(session))
                result=session.act('pilot:A',{'action_id':'choose','targets':[bodies[0].ref]});self.assertTrue(result.ok,result.summary)
                self.assertEqual(1,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
            else:self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
            self.assertEqual(qualifies,'flying' in session.engine._combat_keywords(session.state.cards[bodies[0].object_id]))
            self.replay(session,load=True)

    def test_actual_jolene_numeric_qualification_survives_later_power_reduction(self):
        session=self.session(508631070);source=self.add(session.engine,'Jolene, Plundering Pugilist',zone='hand')
        large=self.add(session.engine,'Shivan Dragon');small=self.add(session.engine,'Grizzly Bears')
        self.add(session.engine,'Wastes',seat='B')
        # Humility changes power after the declaration through an actual flash cast.
        shrink=self.add(session.engine,'Humility',seat='B',zone='hand');self.add(session.engine,'Vedalken Orrery',seat='B')
        self.add(session.engine,'Plains',seat='B',ref='shrink-white-1');self.add(session.engine,'Plains',seat='B',ref='shrink-white-2');self.add(session.engine,'Wastes',seat='B',ref='shrink-generic-2')
        for card in (large,small):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':1,'G':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.attack(session,[(large,'B'),(small,'C')])
        self.assertEqual(1,self.trigger_count(session,source));self.cast(session,'B',shrink)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.engine._numeric_stat(large.object_id,'power'))
        self.assertEqual(1,sum(c.zone=='battlefield' and c.is_token for c in session.state.cards.values()))
        self.replay(session,load=True)

    def test_actual_metropolis_accepts_any_counter_and_keeps_one_draw_after_counter_removal(self):
        session=self.session(508631080);source=self.add(session.engine,'Metropolis Angel',zone='hand')
        counted=self.add(session.engine,'Grizzly Bears');counted.counters['charge']=1
        plain=self.add(session.engine,'Grizzly Bears',ref='uncounted-attacker');remover=self.add(session.engine,'Vampire Hexmage',seat='B')
        for card in (counted,plain):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));hand=len(session.state.players['A'].zones['hand'])
        self.attack(session,[(counted,'B'),(plain,'C')]);self.assertEqual(1,self.trigger_count(session,source))
        self.activate(session,'B',remover,[counted]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[counted.object_id].counters);self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_actual_ancestor_keeps_one_trigger_but_counts_current_attackers_at_resolution(self):
        session=self.session(508631090);source=self.add(session.engine,'Ancestor Dragon',zone='hand')
        bodies=[self.add(session.engine,'Grizzly Bears',ref='ancestor-attacker-'+str(i)) for i in range(3)]
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        for card in bodies:card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'W':2});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life=session.state.players['A'].life
        self.attack(session,[(bodies[0],'B'),(bodies[1],'C'),(bodies[2],'D')]);self.assertEqual(1,self.trigger_count(session,source))
        self.cast(session,'B',removal,[bodies[0].ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(life+2,session.state.players['A'].life);self.replay(session,load=True)
