"""CR 120.4: one instruction over two closed, untargeted recipient sets."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_bound_effect_programs import record
import test_announced_x_additional_costs as controls
from quorune.carddb import CardDatabase
from quorune.compiler import damage_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CompoundDamageCompilerTests(unittest.TestCase):
    def test_existing_damage_and_predicate_authority_withdrawal_keeps_composition_residual(self):
        for cap in ('damage.batch.fixed_set','damage.result.player_life','target.permanent.characteristic_predicate'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn compound recipient authority'])
            text='Generic Bound Compiler Fixture deals 2 damage to each creature without flying and each player.'
            ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=CapabilityRegistry(raw),
                capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            self.assertTrue(ir.material_residuals)

    def test_qualified_creature_group_and_public_second_group_form_one_damage_set(self):
        for tail in ('each player','each planeswalker'):
            text='Generic Bound Compiler Fixture deals 2 damage to each creature without flying and '+tail+'.'
            template=damage_templates.fixed_damage_effect_template(text,card_name='Generic Bound Compiler Fixture')
            self.assertIsNotNone(template)
            self.assertEqual(2,len(template.spec.groups))
            self.assertEqual(('flying',),template.spec.groups[0].query.keywords_none)
            if tail=='each player':self.assertIsInstance(template.spec.groups[1],PlayerDamageGroup)
            else:self.assertEqual(('planeswalker',),template.spec.groups[1].query.types_all)
            self.assertEqual(template.spec,FixedDamageSetSpec.from_dict(template.spec.to_dict()))

    def test_fixed_and_announced_amounts_preserve_whole_source_and_existing_authority(self):
        registry=load_default_capability_registry()
        for amount in ('2','X'):
            text='Generic Bound Compiler Fixture deals '+amount+' damage to each creature without flying and each player.'
            source=replace(record(text,'Sorcery'),mana_cost='{X}{R}' if amount=='X' else '{R}')
            ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            for node in ir.faces[0].nodes:
                self.assertEqual(node.text,text[node.span.start:node.span.end])
                self.assertIn('damage.result.player_life',node.capability_dependencies)
                self.assertIn('target.permanent.characteristic_predicate',node.capability_dependencies)

    def test_ambiguous_targeted_extra_or_repeated_groups_and_riders_stay_residual(self):
        for recipient in ('each creature target player controls and each player',
                'each creature without flying and each player and each planeswalker',
                'each planeswalker and each planeswalker','each creature and each creature',
                'each creature without NotARealKeyword and each player',
                'each creature without flying and each player or planeswalker'):
            text='Generic Bound Compiler Fixture deals 2 damage to '+recipient+'.'
            ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry(),
                capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)
            self.assertTrue(ir.material_residuals)
        text="Generic Bound Compiler Fixture deals 3 damage to each creature without flying and each player. Creatures dealt damage this way can't be regenerated this turn."
        ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry(),
            capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)


class CompoundDamageRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AnnouncedAdditionalXRuntimeTests.session
    add=controls.AnnouncedAdditionalXRuntimeTests.add
    prepare=controls.AnnouncedAdditionalXRuntimeTests.prepare
    checkpoint=controls.AnnouncedAdditionalXRuntimeTests.checkpoint
    cast=controls.AnnouncedAdditionalXRuntimeTests.cast
    resolve=controls.AnnouncedAdditionalXRuntimeTests.resolve
    replay=controls.AnnouncedAdditionalXRuntimeTests.replay
    binding=controls.AnnouncedAdditionalXRuntimeTests.binding
    private_replay=controls.AnnouncedAdditionalXRuntimeTests.private_replay
    actions=controls.AnnouncedAdditionalXRuntimeTests.actions
    action=controls.AnnouncedAdditionalXRuntimeTests.action

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'compound-damage.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/compound-damage-recipient-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Compound damage witnesses',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def test_original_earthquake_includes_all_players_and_grounded_hexproof_without_targets(self):
        session=self.session(729101)
        source=self.add(session.engine,'Earthquake',zone='hand')
        ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='earthquake-ground')
        ground.counters['hexproof']=1
        flyer=self.add(session.engine,'Generic Bound Body',seat='D',ref='earthquake-flyer')
        flyer.counters['flying']=1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:C',{'action_id':action['id'],'x':2,'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        denied=session.act('pilot:A',{'action_id':action['id'],'x':2,'targets':['B'],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual([],session.state.stack[-1].targets)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertTrue(all(p.life==40 for p in session.state.players.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([38]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(2,session.state.cards[ground.object_id].marked_damage)
        self.assertEqual(0,session.state.cards[flyer.object_id].marked_damage)
        self.private_replay(session,private)

    def test_executable_union_rejects_undeclared_registered_handler_dependency(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(729110)
        source=self.add(session.engine,'Earthquake',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if 'damage.batch.fixed_set' in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        # The registered handler owns the closed recipient-set vocabulary.
        # Finer result/predicate dependencies belong to the compiler's closure;
        # the withdrawal test above protects those source-level requirements.
        for cap in ('damage.batch.fixed_set',):
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_original_magmaquake_unions_overlapping_creature_walkers_once(self):
        session=self.session(729102)
        source=self.add(session.engine,'Magmaquake',zone='hand')
        ground=self.add(session.engine,'Generic Compound Walker',seat='B',ref='magma-ground-walker')
        ground.counters['loyalty']=6
        flyer=self.add(session.engine,'Generic Compound Walker',seat='D',ref='magma-flying-walker')
        flyer.counters.update({'loyalty':6,'flying':1})
        creature=self.add(session.engine,'Generic Bound Body',seat='A',ref='magma-flying-creature')
        creature.counters['flying']=1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (source,ground,flyer):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.prepare(session,mana={'R':2,'C':2});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (ground,flyer):
            self.assertEqual(2,session.state.cards[card.object_id].marked_damage)
            self.assertEqual(4,session.state.cards[card.object_id].counters['loyalty'])
        self.assertEqual(0,session.state.cards[creature.object_id].marked_damage)
        self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
        self.private_replay(session,private)

    def test_original_hurricane_uses_the_positive_keyword_group_and_zero_amount(self):
        for x in (0,2):
            session=self.session(729103)
            source=self.add(session.engine,'Hurricane',zone='hand')
            ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='hurricane-ground')
            flyer=self.add(session.engine,'Generic Bound Body',seat='D',ref='hurricane-flyer')
            flyer.counters['flying']=1
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'G':1,'C':x});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':x,'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
            self.assertEqual([40-x]*4,[session.state.players[s].life for s in 'ABCD'])
            self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
            self.assertEqual(x,session.state.cards[flyer.object_id].marked_damage)
            self.private_replay(session,private)

    def test_actual_flying_grant_before_resolution_changes_original_earthquake_recipients(self):
        session=self.session(729104)
        source=self.add(session.engine,'Earthquake',zone='hand')
        mentor=self.add(session.engine,'Generic Bound Mentor')
        body=self.add(session.engine,'Generic Bound Body',ref='late-flying-recipient')
        ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='late-ground-control')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        for card in (source,mentor):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        action=next(r for r in self.actions(session) if r['id'].startswith('activate:'+mentor.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertEqual(0,session.state.cards[body.object_id].marked_damage)
        self.assertEqual(2,session.state.cards[ground.object_id].marked_damage)
        self.assertEqual([38]*4,[session.state.players[s].life for s in 'ABCD'])
        self.private_replay(session,private)

    def test_original_earthquake_simultaneously_eliminates_all_four_players(self):
        session=self.session(729105)
        source=self.add(session.engine,'Earthquake',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for player in session.state.players.values():player.life=2
        self.prepare(session,mana={'R':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.game_over);self.assertTrue(session.state.draw)
        self.assertTrue(all(not p.in_game and p.life==0 for p in session.state.players.values()))
        self.assertFalse(session.state.stack);self.assertIsNone(session.state.priority_player)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_countered_original_earthquake_has_no_recipient_results_or_mana_refund(self):
        session=self.session(729106)
        source=self.add(session.engine,'Earthquake',zone='hand')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        body=self.add(session.engine,'Generic Bound Body',seat='D')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'R':1,'C':2});self.checkpoint(session)
        for card in (source,counter):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(0,session.state.cards[body.object_id].marked_damage)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_thunder_counts_current_beast_permanents_including_kindred(self):
        session=self.session(729107)
        source=self.add(session.engine,'Thunder of Hooves',zone='hand')
        beast=self.add(session.engine,'Generic Compound Beast',seat='B',ref='counted-foreign-beast')
        kindred=self.add(session.engine,'Generic Compound Kindred',seat='C',ref='counted-kindred')
        late=self.add(session.engine,'Generic Compound Beast',zone='hand',ref='late-flash-beast')
        self.add(session.engine,'Generic Compound Beast',seat='D',zone='graveyard',ref='excluded-grave-beast')
        self.add(session.engine,'Generic Compound Beast',seat='D',zone='hand',ref='excluded-hand-beast')
        flyer=self.add(session.engine,'Generic Bound Body',seat='D');flyer.counters['flying']=1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':4});self.checkpoint(session)
        for card in (source,beast,kindred,late):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        action=self.action(session,source)
        for option in action.get('cost_options',[]):self.assertNotIn('x',option.get('choice_schema',{}))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'A',late)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([37]*4,[session.state.players[s].life for s in 'ABCD'])
        for card in (beast,late):self.assertEqual(3,session.state.cards[card.object_id].marked_damage)
        self.assertEqual(0,session.state.cards[kindred.object_id].marked_damage)
        self.assertEqual(0,session.state.cards[flyer.object_id].marked_damage)
        self.private_replay(session,private)

    def test_original_rockcaster_keeps_both_groups_after_actual_source_destruction(self):
        session=self.session(729108)
        source=self.add(session.engine,'Rockcaster Platoon')
        murder=self.add(session.engine,'Murder',seat='B',zone='hand')
        flyer=self.add(session.engine,'Generic Bound Body',seat='D');flyer.counters['flying']=1
        ground=self.add(session.engine,'Generic Bound Body',seat='A',ref='rockcaster-ground')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'B':2,'C':1})
        self.prepare(session,mana={'G':1,'C':4});self.checkpoint(session)
        for card in (source,murder):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        action=next(r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',murder,[source.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual([38]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(2,session.state.cards[flyer.object_id].marked_damage)
        self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
        self.private_replay(session,private)

    def test_original_howling_gale_flashback_keeps_groups_and_exiles_the_paid_spell(self):
        session=self.session(729109)
        source=self.add(session.engine,'Howling Gale',zone='graveyard')
        flyer=self.add(session.engine,'Generic Bound Body',seat='D');flyer.counters['flying']=1
        ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='flashback-ground')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        result=session.act('pilot:A',{'action_id':action['id'],'cost_option':'flashback','pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([39]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(1,session.state.cards[flyer.object_id].marked_damage)
        self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_cloudthresher_evoke_orders_preserve_both_damage_groups_and_source_lki(self):
        for sacrifice_first in (False,True):
            session=self.session(729111)
            source=self.add(session.engine,'Cloudthresher',zone='hand')
            flyer=self.add(session.engine,'Generic Bound Body',seat='D');flyer.counters['flying']=1
            ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='evoke-ground')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'G':2,'C':2});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'evoke','pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            pending=self.resolve(session);self.assertIsNotNone(pending);self.assertEqual('trigger.order',pending.kind)
            rows=session.packet('pilot:A',full=True)['decision']['ctx']['triggers']
            self.assertEqual(2,len(rows))
            evoke=next(r['id'] for r in rows if 'evoke sacrifice' in r['label'])
            damage=next(r['id'] for r in rows if r['id']!=evoke)
            session=self.replay(session,load=True)
            result=session.act('pilot:A',{'action_id':'order','order':[damage,evoke] if sacrifice_first else [evoke,damage]})
            self.assertTrue(result.ok,result.summary)
            for _ in range(12):
                if len(session.state.stack)==1:break
                result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                self.assertTrue(result.ok,result.summary)
            self.assertEqual(1,len(session.state.stack))
            self.assertEqual('graveyard' if sacrifice_first else 'battlefield',session.state.cards[source.object_id].zone)
            self.assertEqual([40 if sacrifice_first else 38]*4,[session.state.players[s].life for s in 'ABCD'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
            self.assertEqual([38]*4,[session.state.players[s].life for s in 'ABCD'])
            self.assertEqual(2,session.state.cards[flyer.object_id].marked_damage)
            self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
            self.private_replay(session,private)

    def test_losing_the_player_group_is_killed_by_a_fresh_native_earthquake(self):
        original=damage_templates._compound_public_mass_damage_spec
        def omit_second(text):
            parsed=original(text)
            if parsed is None:return None
            return FixedDamageSetSpec((parsed[0].groups[0],)),False,False
        with patch.object(damage_templates,'_compound_public_mass_damage_spec',side_effect=omit_second):
            method='test_original_earthquake_includes_all_players_and_grounded_hexproof_without_targets'
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_losing_the_grounded_filter_is_killed_by_a_fresh_native_earthquake(self):
        original=damage_templates._compound_public_mass_damage_spec
        def omit_filter(text):
            parsed=original(text)
            if parsed is None:return None
            first,*remaining=parsed[0].groups
            first=replace(first,query=replace(first.query,keywords_none=()))
            return FixedDamageSetSpec((first,*remaining)),False,False
        with patch.object(damage_templates,'_compound_public_mass_damage_spec',side_effect=omit_filter):
            method='test_original_earthquake_includes_all_players_and_grounded_hexproof_without_targets'
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
