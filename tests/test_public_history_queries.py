"""Typed current-turn history conditions keep occurrence custody and time."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_qualified_attack_subjects as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import TurnHistory,TurnHistoryEvent
from quorune.continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind
from quorune.public_history_query_model import PublicHistoryQuerySpec,public_history_query_value,PUBLIC_HISTORY_QUERY_CAPABILITY as CAPABILITY
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from quorune.public_history_query_model import NONTOKEN_CREATURE_ENTRY_HISTORY_CAPABILITY, current_nontoken_creature_entry_query
from quorune.turn_history import reset_turn_history
from test_controller_creature_keyword_absence import register_card


ENTRY_HISTORY_FOOD = "Gyome, Master Chef"


class PublicHistoryQueryCompilerTests(unittest.TestCase):
    def test_new_history_schema_round_trips_without_changing_legacy_payloads(self):
        spec=PublicHistoryQuerySpec('creature_died','controller')
        condition=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.HISTORY_COUNT_AT_LEAST,amount=1,schema_version=8,history_query=spec)
        self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
        old=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST,amount=20)
        self.assertEqual({'schema_version':1,'kind':'controller_life_at_least','amount':20,'counter_name':None},old.to_dict())
        for change in ({'schema_version':True},{'schema_version':7},{'amount':True},{'kind':'controller_life_at_least'},
                {'extra':None},{'counter_name':'charge'}):
            with self.assertRaises(ValueError):FixedPublicStateConditionSpec.from_dict({**condition.to_dict(),**change})
        for change in ({'event':'card_drawn_secretly'},{'relation':'owner'},{'schema_version':True},
                {'reduction':'sum_amount'},{'types_all':['unknown']},{'extra':None}):
            with self.assertRaises(ValueError):PublicHistoryQuerySpec.from_dict({**spec.to_dict(),**change})

    def test_history_queries_use_sealed_roles_and_per_player_amounts_without_current_objects(self):
        history=TurnHistory(turn_sequence=4,events=[
            TurnHistoryEvent('creature_died',actor='A',object_incarnation='borrowed@1',types=('artifact','creature')),
            TurnHistoryEvent('creature_died',actor='B',object_incarnation='other@1',types=('creature',)),
            TurnHistoryEvent('permanent_entered',actor='A',object_incarnation='one@1',types=('artifact',)),
            TurnHistoryEvent('permanent_entered',actor='A',object_incarnation='one@3',types=('artifact','creature')),
            TurnHistoryEvent('player_lost_life',target='B',amount=2),TurnHistoryEvent('player_lost_life',target='C',amount=3),
            TurnHistoryEvent('player_gained_life',target='A',amount=2),TurnHistoryEvent('player_lost_life',target='A',amount=2)])
        state=SimpleNamespace(turn_history=history,turn_sequence=4,players={s:SimpleNamespace(in_game=True) for s in 'ABCD'},cards={})
        before=history.to_dict()
        self.assertEqual(1,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died')))
        self.assertEqual(2,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died','any_player')))
        self.assertEqual(2,public_history_query_value(state,'A',PublicHistoryQuerySpec('permanent_entered',types_all=('artifact',))))
        self.assertEqual(3,public_history_query_value(state,'A',PublicHistoryQuerySpec('player_lost_life','opponents',reduction='max_player_amount')))
        self.assertEqual(5,public_history_query_value(state,'A',PublicHistoryQuerySpec('player_lost_life','opponents',reduction='sum_amount')))
        self.assertEqual(before,history.to_dict())
        state.players['B'].in_game=False
        self.assertEqual(2,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died','any_player')))
        self.assertEqual(0,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died','opponents')))
        for missing in (None,TurnHistory(turn_sequence=3)):
            state.turn_history=missing;self.assertIsNone(public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died')))
        state.turn_history=history
        self.assertIsNone(public_history_query_value(state,'A',PublicHistoryQuerySpec('permanent_left')))

    def test_current_turn_history_grammar_composes_with_independent_trigger_results(self):
        registry=load_default_capability_registry()
        for condition in ('a creature died under your control this turn','three or more artifacts entered the battlefield under your control this turn',
                'two or more creatures entered the battlefield under your control this turn','you gained or lost life this turn',
                'a player lost 4 or more life this turn','you sacrificed a creature this turn'):
            parsed=fixed_public_state_condition(condition,source_name='Generic Observer');self.assertIsNotNone(parsed,condition)
            text='At the beginning of your end step, if '+condition+', draw a card.'
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(text,ir.material_residuals));self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        combined=fixed_public_state_condition('a creature died under your control this turn and you gained life this turn',source_name='Generic Observer')
        self.assertIsNotNone(combined);self.assertEqual(2,len(combined.terms))
        for condition in ('a creature died under your control last turn','you will gain life this turn','a creature of the chosen type died this turn'):
            self.assertIsNone(fixed_public_state_condition(condition,source_name='Generic Observer'))

    def test_ignoring_sealed_event_actor_mutant_is_killed(self):
        import quorune.public_history_query_model as owner
        state=SimpleNamespace(turn_sequence=1,players={s:SimpleNamespace(in_game=True) for s in 'AB'},
            turn_history=TurnHistory(turn_sequence=1,events=[TurnHistoryEvent('creature_died',actor='B',object_incarnation='foreign@1',types=('creature',))]))
        spec=PublicHistoryQuerySpec('creature_died','controller')
        def witness():self.assertEqual(0,owner.public_history_query_value(state,'A',spec))
        witness()
        with patch.object(owner,'public_history_query_value',return_value=1),self.assertRaises(AssertionError):witness()

    def test_verified_history_query_withdrawal_preserves_unrelated_flying(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in value['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['history query owner withdrawn'])
        registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        text='At the beginning of your end step, if a creature died under your control this turn, draw a card.'
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Flying'),capability_registry=registry).status)


class PublicHistoryQueryNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.QualifiedAttackNativeTests.session
    add=native.QualifiedAttackNativeTests.add
    prepare=native.QualifiedAttackNativeTests.prepare
    cast=native.QualifiedAttackNativeTests.cast
    cast_for=native.QualifiedAttackNativeTests.cast_for
    priority=native.QualifiedAttackNativeTests.priority
    action=native.QualifiedAttackNativeTests.action
    checkpoint=native.QualifiedAttackNativeTests.checkpoint
    resolve=native.QualifiedAttackNativeTests.resolve
    replay=native.QualifiedAttackNativeTests.replay
    assert_closed=native.QualifiedAttackNativeTests.assert_closed
    until=native.QualifiedAttackNativeTests.until
    activate=native.QualifiedAttackNativeTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'public-history.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/public-history-query-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public history',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_runtime_history_query_dependency_is_inferred_from_intervening_condition(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603632001);source=self.add(session.engine,'Denethor, Ruling Steward');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def end_step(self,session):
        self.until(session,lambda s:s.state.phase=='ending' and s.state.step=='end_step')
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
            decision=session.state.pending_decision
            group=next(g for g in session.state.pending_trigger_batches[0].groups if g.controller==decision.actors[0])
            result=session.act(session.pending_principals()[0],{'action_id':'order','triggers':[i.ref for i in group.items]});self.assertTrue(result.ok,result.summary)

    def test_actual_denethor_controlled_sacrifice_counts_death_and_queued_token_survives_source_bounce(self):
        session=self.session(603632010);source=self.add(session.engine,'Denethor, Ruling Steward',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'W':1,'B':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':2});self.assertIsNone(self.resolve(session))
        self.priority(session,'A');offered=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'cost_cards':[body.ref],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.end_step(session);self.assertTrue(session.state.stack)
        self.cast(session,'B',removal,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));self.replay(session,load=True)

    def test_actual_denethor_exiled_creature_does_not_satisfy_death_condition(self):
        session=self.session(603632020);source=self.add(session.engine,'Denethor, Ruling Steward',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');exile=self.add(session.engine,'Final Reward',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,exile)
        self.prepare(session,mana={'C':1,'W':1,'B':1});session.state.players['B'].mana_pool.update({'C':4,'B':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.cast(session,'B',exile,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[body.object_id].zone);self.end_step(session)
        self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack));self.replay(session,load=True)

    def test_missing_history_is_unavailable_in_zero_count_and_boolean_conditions(self):
        from quorune.card_programs.runtime import fixed_public_state_condition_matches
        from quorune.continuous_conditions import FixedPublicStateConditionError
        session=self.session(603632025);source=self.add(session.engine,'Denethor, Ruling Steward')
        session.state.turn_history=None
        zero=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.HISTORY_COUNT_AT_MOST,amount=0,schema_version=8,
            history_query=PublicHistoryQuerySpec('creature_died'))
        positive=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST,amount=0)
        combined=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.ANY,schema_version=6,terms=(positive,zero))
        for condition in (zero,combined):
            with self.assertRaises(FixedPublicStateConditionError):fixed_public_state_condition_matches(session.state,source,condition,
                public_object_resolver=None,quantity_resolver=None,require_available=True)
        self.assertFalse(fixed_public_state_condition_matches(session.state,source,zero,
            public_object_resolver=None,quantity_resolver=None))

    def test_actual_malcator_counts_repeat_artifact_entries_after_the_object_leaves(self):
        session=self.session(603632030);source=self.add(session.engine,'Malcator, Purity Overseer',zone='hand')
        artifact=self.add(session.engine,'Memnite',zone='hand');blink=self.add(session.engine,'Flicker of Fate',zone='hand')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':2,'U':1});session.state.players['B'].mana_pool['U']=1;self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',artifact,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',blink,[artifact.ref]);self.assertIsNone(self.resolve(session));self.cast(session,'B',bounce,[artifact.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[artifact.object_id].zone)
        self.end_step(session);self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,sum(c.zone=='battlefield' and c.is_token for c in session.state.cards.values()));self.replay(session,load=True)

    def test_actual_soothsayer_life_gain_and_loss_do_not_cancel_history(self):
        session=self.session(603632040);source=self.add(session.engine,'Starlit Soothsayer',zone='hand')
        gain=self.add(session.engine,"Chaplain's Blessing",zone='hand');loss=self.add(session.engine,'Sign in Blood',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'B':3,'W':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'B':2,'W':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',gain,reserve={'B':2});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',loss,['A']);self.assertIsNone(self.resolve(session));self.end_step(session)
        self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        session=self.replay(session,load=True);decision=self.resolve(session);self.assertIsNotNone(decision)
        expected=session.state.cards[session.state.players['A'].zones['library'][-1]].ref
        for seat in 'BCD':self.assertNotIn(expected,json.dumps(session.packet('pilot:'+seat,full=True)))
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':[expected],'graveyard':[]}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_actual_soothsayer_without_life_event_does_not_create_a_trigger(self):
        session=self.session(603632050);source=self.add(session.engine,'Starlit Soothsayer',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'B':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.end_step(session)
        self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack));self.replay(session,load=True)


    def cast_with_mana(self, game, actor, card, targets=(), payment=None):
        self.priority(game, actor)
        command = {"action_id": "cast:" + card.ref, "targets": list(targets), "pay": "auto"}
        if payment is not None:
            command.update(pay="manual", payment=payment)
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def food_tokens(self, game):
        return tuple(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"
                     and card.controller == "A" and "food" in game.engine._type_parts(
                         str(game.engine._effective_card_data(card)["type_line"]))[1])

    def test_food_counts_earlier_and_resolving_creature_entries_and_retains_paid_protection(self):
        game = self.session(60820091)
        source = self.add(game.engine, ENTRY_HISTORY_FOOD, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", zone="hand", ref="entry-repeat-bear")
        tapped = self.add(game.engine, "Grizzly Bears", seat="C", ref="entry-already-tapped-bear")
        tapped.tapped = True
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        token_spell = self.add(game.engine, "Raise the Alarm", zone="hand")
        late = self.add(game.engine, "Cloudshift", zone="hand")
        foreign = self.add(game.engine, "Ashcoat Bear", seat="B", zone="hand")
        for index in range(2):
            self.add(game.engine, "Plains", ref=f"entry-end-step-plains-{index}")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "G": 3, "B": 1, "U": 1, "W": 1})
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["B"].mana_pool.update(C=1, G=1)
        self.checkpoint(game)

        for actor, card, targets, payment in (
            ("A", bear, [], {"C": 1, "G": 1}),
            ("A", bounce, [bear.ref], {"U": 1}),
            ("A", source, [], {"C": 2, "B": 1, "G": 1}),
            ("A", bear, [], {"C": 1, "G": 1}),
            ("A", token_spell, [], {"C": 1, "W": 1}),
            ("B", foreign, [], {"C": 1, "G": 1}),
        ):
            self.cast_with_mana(game, actor, card, targets, payment)
            self.assertIsNone(self.resolve(game))
        query = current_nontoken_creature_entry_query()
        self.assertEqual(3, public_history_query_value(game.state, "A", query))
        self.assertEqual(1, public_history_query_value(game.state, "B", query))
        self.assertEqual(2, sum(event.kind == "permanent_entered" and event.token is True
                                for event in game.state.turn_history.events))
        self.end_step(game)
        trigger, = game.state.stack
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.cast_with_mana(game, "A", late, [bear.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, public_history_query_value(game.state, "A", query))
        self.assertEqual(4, len(self.food_tokens(game)))

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        food = self.food_tokens(game)[0]
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": [tapped.ref],
                                     "cost_cards": [food.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        protected = game.state.cards[tapped.object_id]
        self.assertTrue(protected.tapped)
        self.assertIn("indestructible", game.engine._combat_keywords(protected))
        self.assertEqual(3, len(self.food_tokens(game)))
        self.replay(game, load=True)

    def test_food_count_with_no_current_creature_entries_is_zero(self):
        game = self.session(60820092)
        source = self.add(game.engine, ENTRY_HISTORY_FOOD)
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.end_step(game)
        self.assertTrue(any(item.source_object_id == source.object_id for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((), self.food_tokens(game))
        self.replay(game, load=True)

    def test_old_entry_journal_payload_replays_and_unavailable_token_facts_fail_closed(self):
        game = self.session(60820093)
        bear = self.add(game.engine, "Grizzly Bears", zone="hand")
        tokens = self.add(game.engine, "Raise the Alarm", zone="hand")
        game.state.turn_history.permanent_entry_token_history_version = None
        self.prepare(game, mana={"C": 2, "G": 1, "W": 1})
        self.checkpoint(game)
        for card in (bear, tokens):
            self.cast_with_mana(game, "A", card)
            self.assertIsNone(self.resolve(game))
        history = game.state.turn_history
        entries = [event for event in history.events if event.kind == "permanent_entered"]
        self.assertEqual(3, len(entries))
        self.assertTrue(all("token" not in entry.to_dict() for entry in entries))
        self.assertNotIn("permanent_entry_token_history_version", history.to_dict())
        self.assertIsNone(reset_turn_history(history, history.turn_sequence + 1).permanent_entry_token_history_version)
        query = current_nontoken_creature_entry_query()
        self.assertIsNone(public_history_query_value(game.state, "A", query))
        game = self.replay(game, load=True)
        self.assertEqual(history.to_dict(), game.state.turn_history.to_dict())

        incomplete = replace(history, permanent_entry_token_history_version=1)
        snapshot = SimpleNamespace(turn_history=incomplete, turn_sequence=history.turn_sequence,
                                   players=game.state.players)
        self.assertIsNone(public_history_query_value(snapshot, "A", query))
        with self.assertRaises(ValueError):
            TurnHistoryEvent.from_dict({**entries[0].to_dict(), "token": 1})

    def test_food_amount_requires_its_exact_history_capability_and_result(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec

        report, semantics = register_card(self.db, self.registry, ENTRY_HISTORY_FOOD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        cap = NONTOKEN_CREATURE_ENTRY_HISTORY_CAPABILITY
        programs = semantics.programs_for_oracle(report["oracle_id"])
        trigger, = (program for program in programs if cap in program.capability_dependencies)
        effect, = trigger.effects
        amount = ScalarEffectAmountSpec.from_dict(effect["quantity"])
        self.assertEqual(current_nontoken_creature_entry_query(), amount.history_count)
        dependencies = tuple(value for value in trigger.capability_dependencies if value != cap)
        altered = replace(trigger, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + cap, bound["blockers"])
        source = self.db.lookup(ENTRY_HISTORY_FOOD, fuzzy=False)
        for old, new in (("nontoken creatures", "noncreature permanents"),
                         ("this turn", "last turn"), ("a number of Food tokens", "a number of Treasure tokens"),
                         ("your end step", "an opponent's end step")):
            changed = replace(source, oracle_text=source.oracle_text.replace(old, new))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)
