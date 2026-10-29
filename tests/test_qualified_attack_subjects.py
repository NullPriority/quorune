"""CR508.2a: qualified attack occurrences use sealed public characteristics."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_bound_static_declarations as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.qualified_attack_model import QUALIFIED_ATTACK_CAPABILITY as CAPABILITY,ATTACK_FACT_PREFIX as PREFIX
from quorune.rules.qualified_attacks import qualified_attack_runtime_dependencies
from scripts.build_test_database import build_fixture_database


class QualifiedAttackCompilerTests(unittest.TestCase):
    def test_closed_subject_queries_share_exact_effects_and_keep_singular_scope(self):
        registry=load_default_capability_registry()
        for subject,alone in (('a Dragon you control',''),('a white creature you control',''),
                ('a creature you control with haste',''),('a modified creature you control',''),
                ('a creature you control with a +1/+1 counter on it',''),
                ('a Samurai or Warrior you control',' alone'),('another nontoken creature you control','')):
            text=f'Whenever {subject} attacks{alone}, draw a card.'
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(text,ir.material_residuals))
            node=ir.faces[0].nodes[0];self.assertIn(CAPABILITY,node.capability_dependencies)
            self.assertEqual('creature.attacks',node.event)
            self.assertIn(PREFIX+'version',json.dumps(node.event_condition))
        for text in ('Whenever one or more Dragons you control attack, draw a card.',
                'Whenever a creature you own attacks, draw a card.',
                'Whenever a chosen creature you control attacks, draw a card.',
                'Whenever a Dragon you control attacks, rewrite history.',
                'Whenever a Dragon you control attacks a player, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def test_sealed_fact_requirements_reject_malformed_fields_without_widening_old_damage(self):
        from quorune.compiler.fixed_public_action_event_bindings import _qualified_damage_singular_spec
        old=_qualified_damage_singular_spec('Whenever a blue creature you control deals combat damage to a player, draw a card.')
        self.assertIsNotNone(old)
        self.assertFalse(any(PREFIX in str(v) for v in old.condition.values()))
        self.assertIsNone(_qualified_damage_singular_spec('Whenever a modified creature you control deals combat damage to a player, draw a card.'))
        valid={'all':[{'field':PREFIX+'version','op':'eq','value':1},
            {'field':PREFIX+'subtypes','op':'contains_any','value':['dragon']}]}
        self.assertEqual((CAPABILITY,),qualified_attack_runtime_dependencies(SimpleNamespace(event='creature.attacks',event_condition=valid)))
        for changed,event in (({'field':PREFIX+'version','op':'eq','value':True},'creature.attacks'),
                ({'field':PREFIX+'version','op':'eq','value':2},'creature.attacks'),
                ({'field':PREFIX+'subtypes','op':'contains_any','value':['dragon']},'creature.attacks'),
                ({'all':[valid,{'field':PREFIX+'private_hand','op':'eq','value':[]}]},'creature.attacks'),
                (valid,'permanent.enter')):
            with self.assertRaises(ValueError):qualified_attack_runtime_dependencies(SimpleNamespace(event=event,event_condition=changed))

    def test_verified_attack_owner_withdrawal_blocks_qualified_sources_and_preserves_plain_flying(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(r for r in value['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['qualified attack owner withdrawn'])
        registry=CapabilityRegistry(value);index=build_index(registry_value=value,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(query_record('Whenever a Dragon you control attacks, draw a card.'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Flying'),capability_registry=registry).status)

    def test_omitted_sealed_subtype_mutant_is_killed_at_the_event_boundary(self):
        from quorune.model import CardInstance
        from quorune.object_query import ObjectQueryResult
        from quorune.trigger_discovery import semantic_event_condition_matches
        import quorune.rules.qualified_attacks as owner
        source=CardInstance('observer','observer-ref','fixture:observer','Generic Observer','A','A','battlefield')
        actor=CardInstance('actor','actor-ref','fixture:actor','Generic Actor','B','A','battlefield')
        row=ObjectQueryResult('actor','actor-ref','Generic Actor','B','A','battlefield',types=('creature',),subtypes=('dragon',))
        host=SimpleNamespace(_public_object_query_result=lambda card:row,state=SimpleNamespace(active_player='A',qualified_attack_facts_version=1))
        ir=compile_oracle_card(query_record('Whenever a Dragon you control attacks, draw a card.'),capability_registry=load_default_capability_registry())
        condition=ir.faces[0].nodes[0].event_condition
        def witness():
            context={'card':actor.ref,'controller':'A',**owner.attack_characteristic_context(host,actor)}
            self.assertTrue(semantic_event_condition_matches(host,condition,source=source,context=context))
        witness();original=owner.attack_characteristic_context
        def mutant(*args):return {k:v for k,v in original(*args).items() if k!=PREFIX+'subtypes'}
        with patch.object(owner,'attack_characteristic_context',side_effect=mutant),self.assertRaises(AssertionError):witness()

    def test_attacker_result_pronoun_and_explicit_observer_keep_distinct_sealed_roles(self):
        registry=load_default_capability_registry()
        prefix='Whenever a Samurai or Warrior you control attacks alone, '
        for subject,expected in (('it','$event.permanent_object'),('This creature','$source.zone_object')):
            text=prefix+subject+' gets +1/+0 until end of turn for each Samurai or Warrior you control.'
            record=replace(query_record(text),type_line='Creature — Human Samurai',power='4',toughness='3')
            ir=compile_oracle_card(record,capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals);self.assertEqual(expected,ir.faces[0].nodes[0].effects[0]['card'])
        text=prefix+'put a +1/+1 counter on it.'
        ir=compile_oracle_card(query_record(text),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals);self.assertEqual('$event.counter_object',ir.faces[0].nodes[0].effects[0]['card'])

    def test_attack_result_owner_withdrawal_keeps_unrelated_qualified_token_result(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(r for r in value['capabilities'] if r['id']==ATTACK_CHARACTERISTIC_RESULT_CAPABILITY).update(status='blocked',blockers=['attack occurrence result withdrawn'])
        registry=CapabilityRegistry(value);index=build_index(registry_value=value,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        text='Whenever a Samurai or Warrior you control attacks alone, it gets +1/+0 until end of turn for each Samurai or Warrior you control.'
        record=replace(query_record(text),type_line='Creature — Human Samurai')
        self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Whenever a Dragon you control attacks, create a 6/6 red Dragon creature token with flying.'),capability_registry=registry).status)

    def test_omitted_attacker_result_binding_mutant_is_killed(self):
        registry=load_default_capability_registry()
        text='Whenever a Samurai or Warrior you control attacks alone, it gets +1/+0 until end of turn for each Samurai or Warrior you control.'
        record=replace(query_record(text),type_line='Creature — Human Samurai')
        def witness():self.assertEqual('$event.permanent_object',compile_oracle_card(record,capability_registry=registry).faces[0].nodes[0].effects[0]['card'])
        witness()
        with patch('quorune.compiler.attack_event_results.qualified_attack_characteristic_result',return_value=None),self.assertRaises(AssertionError):witness()

    def test_occurrence_characteristic_shape_rejects_unbound_relative_quantities_and_changed_roles(self):
        from quorune.rules.attack_event_results import attack_event_characteristic_capabilities
        registry=load_default_capability_registry()
        text='Whenever a Samurai or Warrior you control attacks alone, it gets +1/+0 until end of turn for each Samurai or Warrior you control.'
        record=replace(query_record(text),type_line='Creature — Human Samurai')
        node=compile_oracle_card(record,capability_registry=registry).faces[0].nodes[0]
        self.assertTrue(attack_event_characteristic_capabilities(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics))
        for mutate in (lambda e:e.update(card='$context.card'),lambda e:e.update(extra=None),
                lambda e:e['power']['quantity'].update(exclude_source=True),lambda e:e['power'].update(schema_version=True)):
            effect=deepcopy(node.effects[0]);mutate(effect)
            self.assertEqual((),attack_event_characteristic_capabilities(effects=(effect,),target_schema=None,mechanic_ids=node.mechanics))
        self.assertNotEqual('exact',compile_oracle_card(replace(record,oracle_text=text.replace('for each Samurai','for each other Samurai')),capability_registry=registry).status)


class QualifiedAttackNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.BoundStaticDeclarationNativeTests.session
    add=native.BoundStaticDeclarationNativeTests.add
    prepare=native.BoundStaticDeclarationNativeTests.prepare
    cast=native.BoundStaticDeclarationNativeTests.cast
    cast_for=native.BoundStaticDeclarationNativeTests.cast_for
    action=native.BoundStaticDeclarationNativeTests.action
    priority=native.BoundStaticDeclarationNativeTests.priority
    checkpoint=native.BoundStaticDeclarationNativeTests.checkpoint
    resolve=native.BoundStaticDeclarationNativeTests.resolve
    replay=native.BoundStaticDeclarationNativeTests.replay
    assert_closed=native.BoundStaticDeclarationNativeTests.assert_closed
    until=native.BoundStaticDeclarationNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-attacks.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-attack-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified attacks',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def attack(self,session,assignments):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        packet=session.packet('pilot:A',full=True);self.assertEqual('combat.attackers',packet['decision']['kind'])
        command={'action_id':'attack','attackers':{card.ref:seat for card,seat in assignments}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
            refs=[item.ref for batch in session.state.pending_trigger_batches for item in batch.items]
            result=session.act(session.pending_principals()[0],{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)

    def trigger_count(self,session,source):
        return sum(item.kind=='triggered_ability' and item.source_object_id==source.object_id for item in session.state.stack)

    def test_actual_utvara_counts_each_controlled_dragon_and_queued_tokens_survive_source_bounce(self):
        session=self.session(508629001);source=self.add(session.engine,'Utvara Hellkite',zone='hand')
        dragon=self.add(session.engine,'Shivan Dragon');bear=self.add(session.engine,'Grizzly Bears')
        self.add(session.engine,'Fervor');removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand',ref='qualified-attack-private')
        for card in (dragon,bear):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':6,'R':2});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(source,'B'),(dragon,'C'),(bear,'D')]);self.assertEqual(2,self.trigger_count(session,source))
        self.cast(session,'B',removal,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(2,len(tokens));self.assertFalse(session.state.stack)
        for card in tokens:
            self.assertEqual('A',card.controller);self.assertNotIn(card.object_id,session.state.combat.attackers)
            self.assertEqual((6,6),tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
            self.assertIn('flying',session.engine._combat_keywords(card))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_runtime_qualified_attack_capability_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(508629010);source=self.add(session.engine,'Utvara Hellkite');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_attack_fact_execution_marker_round_trips_and_absence_preserves_historical_state(self):
        from quorune.model import GameState
        from quorune.rules.qualified_attacks import attack_characteristic_context
        session=self.session(508629015);raw=session.state.to_dict()
        self.assertEqual(1,raw['qualified_attack_facts_version'])
        self.assertEqual(raw,GameState.from_dict(raw).to_dict())
        old=deepcopy(raw);old.pop('qualified_attack_facts_version')
        restored=GameState.from_dict(old);self.assertIsNone(restored.qualified_attack_facts_version)
        self.assertEqual(old,restored.to_dict())
        self.assertEqual({},attack_characteristic_context(SimpleNamespace(state=restored),None))
        for version in (True,False,0,2,'1'):
            with self.assertRaises(ValueError):GameState.from_dict({**raw,'qualified_attack_facts_version':version})

    def activate(self,session,seat,card,targets):
        self.priority(session,seat)
        offered=next(row for row in session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+session.state.cards[card.object_id].ref+':'))
        result=session.act('pilot:'+seat,{'action_id':offered['id'],'targets':[c.ref for c in targets],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_actual_linden_seals_white_at_declaration_across_later_white_and_black_changes(self):
        session=self.session(508629020);source=self.add(session.engine,'Linden, the Steadfast Queen')
        white=self.add(session.engine,'Savannah Lions');masked=self.add(session.engine,'Daru Lancer',zone='hand',ref='masked-attacker')
        self.add(session.engine,'Fervor');self.add(session.engine,'Plains')
        self.add(session.engine,'Plains',ref='morph-up-second-white')
        self.add(session.engine,'Wastes',ref='morph-up-mana-1');self.add(session.engine,'Wastes',ref='morph-up-mana-2')
        darkness=self.add(session.engine,'Darkest Hour',seat='B',zone='hand');self.add(session.engine,'Swamp',seat='B')
        self.add(session.engine,'Vedalken Orrery',seat='B')
        white.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.assert_closed(session,masked)
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        offered=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast-morph:'+masked.ref)
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertTrue(session.state.cards[masked.object_id].face_down)
        self.assertEqual([],session.engine._effective_card_data(session.state.cards[masked.object_id])['colors'])
        life=session.state.players['A'].life
        self.attack(session,[(white,'B'),(masked,'C')]);self.assertEqual(1,self.trigger_count(session,source))
        for seat in 'BCD':self.assertNotIn('Daru Lancer',json.dumps(session.packet('pilot:'+seat,full=True)))
        self.cast(session,'B',darkness);self.priority(session,'A')
        offered=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='turn-face-up:'+masked.ref)
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(['W'],session.engine._effective_card_data(session.state.cards[masked.object_id])['colors'])
        self.assertEqual(1,self.trigger_count(session,source))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(life+1,session.state.players['A'].life)
        self.assertEqual(['B'],session.engine._effective_card_data(session.state.cards[white.object_id])['colors'])
        self.assertEqual(['B'],session.engine._effective_card_data(session.state.cards[masked.object_id])['colors'])
        self.replay(session,load=True)

    def test_actual_inkcaster_requires_named_counter_and_queued_result_survives_counter_removal(self):
        session=self.session(508629030);source=self.add(session.engine,'Tenured Inkcaster',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');charge=self.add(session.engine,'Grizzly Bears',ref='charge-counter-attacker');charge.counters['charge']=1
        remover=self.add(session.engine,'Vampire Hexmage',seat='B')
        for card in (body,charge):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'B':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNotNone(self.resolve(session))
        result=session.act('pilot:A',{'action_id':'choose','targets':[body.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[body.object_id].counters.get('+1/+1'))
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        self.attack(session,[(body,'B'),(charge,'C')]);self.assertEqual(1,self.trigger_count(session,source))
        self.activate(session,'B',remover,[body]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[body.object_id].counters)
        self.assertEqual(life['A']+1,session.state.players['A'].life)
        for seat in 'BCD':self.assertEqual(life[seat]-1,session.state.players[seat].life)
        self.replay(session,load=True)

    def test_actual_ognis_uses_haste_at_declaration_and_keeps_queued_tapped_treasures_after_blanking(self):
        session=self.session(508629040);source=self.add(session.engine,"Ognis, the Dragon's Lash",zone='hand')
        goblin=self.add(session.engine,'Raging Goblin');bear=self.add(session.engine,'Grizzly Bears')
        blank=self.add(session.engine,'Dress Down',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.add(session.engine,'Wastes',seat='B')
        for card in (goblin,bear):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'R':4});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(source,'B'),(goblin,'C'),(bear,'D')]);self.assertEqual(2,self.trigger_count(session,source))
        self.cast(session,'B',blank);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(2,len(tokens));self.assertTrue(all(c.tapped for c in tokens))
        self.assertNotIn('haste',session.engine._combat_keywords(session.state.cards[source.object_id]))
        drawn=session.state.cards[session.state.players['B'].zones['hand'][-1]].ref
        for seat in 'ACD':self.assertNotIn(drawn,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_samurai_warrior_union_counts_a_changeling_once_and_alone_counts_all_attackers(self):
        for alone in (True,False):
            session=self.session(508629050+int(alone));source=self.add(session.engine,'Imperial Subduer',zone='hand')
            body=self.add(session.engine,'Universal Automaton');other=self.add(session.engine,'Grizzly Bears')
            target=self.add(session.engine,'Grizzly Bears',seat='B',ref='subduer-opponent-target')
            for card in (body,other):card.acquired_control_turn_count=-1
            self.assert_closed(session,source);self.assert_closed(session,body)
            self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
            self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
            self.attack(session,[(body,'B')]+([] if alone else [(other,'C')]))
            if alone:
                self.assertIsNotNone(self.resolve(session))
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:C',{'action_id':'choose','targets':[target.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
                self.assertEqual(1,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
            else:
                self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
            self.assertEqual(alone,session.state.cards[target.object_id].tapped)
            self.replay(session,load=True)

    def test_actual_asari_buffs_the_different_attacker_and_counts_each_union_member_once(self):
        session=self.session(508630001);source=self.add(session.engine,'Asari Captain',zone='hand')
        body=self.add(session.engine,'Universal Automaton');other=self.add(session.engine,'Goblin Piker')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand',ref='asari-private')
        body.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'R':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(body,'B')]);self.assertEqual(1,self.trigger_count(session,source))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        stats=lambda card:tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness'))
        self.assertEqual((4,1),stats(body));self.assertEqual((4,3),stats(source));self.assertEqual((2,1),stats(other))
        session=self.replay(session,load=True)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))

    def test_actual_asari_queued_result_does_not_follow_a_blinked_attacker(self):
        session=self.session(508630010);source=self.add(session.engine,'Asari Captain',zone='hand')
        body=self.add(session.engine,'Universal Automaton');body.acquired_control_turn_count=-1
        blink=self.add(session.engine,'Flicker of Fate',zone='hand');self.add(session.engine,'Plains');self.add(session.engine,'Wastes')
        self.assert_closed(session,source);self.assert_closed(session,blink)
        self.prepare(session,mana={'C':3,'R':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        identity=session.state.cards[body.object_id].logical_object_id
        self.attack(session,[(body,'B')]);self.assertEqual(1,self.trigger_count(session,source))
        self.cast(session,'A',blink,[body.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotEqual(identity,session.state.cards[body.object_id].logical_object_id)
        self.assertEqual((1,1),tuple(session.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
        self.assertEqual((4,3),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.replay(session,load=True)

    def test_runtime_attack_occurrence_result_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY
        session=self.session(508630020);source=self.add(session.engine,'Asari Captain');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if ATTACK_CHARACTERISTIC_RESULT_CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=ATTACK_CHARACTERISTIC_RESULT_CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+ATTACK_CHARACTERISTIC_RESULT_CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_actual_atarka_grants_double_strike_to_the_other_dragon_and_changes_both_damage_steps(self):
        session=self.session(508630030);source=self.add(session.engine,'Atarka, World Render',zone='hand')
        dragon=self.add(session.engine,'Shivan Dragon');bear=self.add(session.engine,'Grizzly Bears')
        for card in (dragon,bear):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':5,'R':1,'G':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        life={seat:session.state.players[seat].life for seat in 'BC'}
        self.attack(session,[(dragon,'B'),(bear,'C')]);self.assertEqual(1,self.trigger_count(session,source))
        self.assertIsNone(self.resolve(session))
        self.assertIn('double strike',session.engine._combat_keywords(session.state.cards[dragon.object_id]))
        for card in (source,bear):self.assertNotIn('double strike',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.until(session,lambda s:s.state.players['B'].life==life['B']-10 and s.state.players['C'].life==life['C']-2)
        self.replay(session,load=True)
