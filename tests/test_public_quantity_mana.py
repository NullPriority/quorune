from __future__ import annotations

"""Pinned CR605/608: public amounts after costs and source LKI."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

from common import ROOT
from quorune.abilities import ActivatedAbility, parse_activated_abilities
from quorune.carddb import CardDatabase
from quorune.compiler.public_quantity_mana import public_quantity_mana_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_quantity_mana_abilities import compile_public_quantity_activated_mana_ability
from quorune.public_quantity_mana_model import PublicQuantityManaOutput
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, CapabilityRegistryError, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class PublicQuantityManaCompilerTests(unittest.TestCase):
    def test_quantity_mana_compiler_and_codec_close_existing_producers(self):
        for body in ('Add {G} for each creature you control.',
                     'Add {B} for each basic Swamp you control.',
                     'Add an amount of {G} equal to this creature\'s power.',
                     'Add X mana of any one color, where X is the number of charge counters on this artifact.',
                     'Add X mana in any combination of {G} and/or {U}, where X is the number of creatures you control.'):
            with self.subTest(body=body):
                text='{T}: '+body
                record=replace(query_record(text),type_line='Artifact Creature — Druid',power='3',toughness='3')
                ir=compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status)
                node=ir.faces[0].nodes[0]
                self.assertEqual('activated-mana-public-quantity-v1',node.template_id)
                self.assertIn('mana.production.public_quantity',node.capability_dependencies)
                parsed=parse_activated_abilities(card_name=record.name,oracle_text=text)[0]
                output=public_quantity_mana_template(body,source_name=record.name)
                self.assertEqual(output,PublicQuantityManaOutput.from_dict(output.to_dict()))
                spec=compile_public_quantity_activated_mana_ability(parsed,output)
                ability=spec.to_activated_ability()
                self.assertEqual(ability,ActivatedAbility.from_dict(ability.to_dict()))
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
                self.assertEqual(body,ability.effect_text)

    def test_quantity_mana_grammar_cost_and_codec_boundaries(self):
        for text in ('{T}: Add {G} for each card exiled with this artifact.',
                     '{T}: Add {G} for each creature you control. Draw a card.',
                     '{T}, Discard a card: Add {G} for each creature you control.',
                     '−2: Add {R} for each Mountain you control.',
                     '{T}: Add {G} for each chosen creature type you control.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Artifact'),capability_registry=load_default_capability_registry())
                self.assertNotEqual('exact',ir.status)
                self.assertFalse(any(handler.get('handler_id')=='ability.activated.mana.public-quantity.v1'
                    for node in ir.faces[0].nodes for handler in node.handlers))
        output=public_quantity_mana_template('Add {G} for each creature you control.',source_name='Generic Source').to_dict()
        for mutation in ({'schema_version':True},{'colors':['G','G']},{'selection':'arbitrary'},{'extra':0}):
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                PublicQuantityManaOutput.from_dict({**output,**mutation})
        for text in ('Add {R} for each creature you control. You win the game.',
                     'Add X mana in any combination of colors, where X is the number of creatures you control.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact',ir.status)

    def test_quantity_mana_missing_producer_and_handler_dependencies_fail_closed(self):
        source=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('mana.production.public_quantity','quantity_expression.public_query_effect_amount',
                    'quantity_expression.scalar_effect_amount','activation.tap_untap_cost.haste'):
            value={**source,'capabilities':[row for row in source['capabilities'] if row['id']!=cap]}
            if cap != 'mana.production.public_quantity':
                with self.assertRaises(CapabilityRegistryError):CapabilityRegistry(value)
                continue
            registry=CapabilityRegistry(value)
            card=replace(query_record('{T}: Add {G} for each creature you control.'),type_line='Artifact')
            ir=compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
        from quorune.public_quantity_mana_abilities import public_quantity_mana_handler_descriptor
        from quorune.semantic_runtime.mana_abilities import default_fixed_mana_ability_registry
        parsed=parse_activated_abilities(card_name='Generic Mana',oracle_text='{T}: Add {G} for each creature you control.')[0]
        spec=compile_public_quantity_activated_mana_ability(parsed,public_quantity_mana_template(parsed.effect_text,source_name='Generic Mana'))
        descriptor=public_quantity_mana_handler_descriptor(spec)
        for delta in ({'schema_version':True},{'event':'resolve'},{'extra':0}):
            with self.assertRaises(ValueError):default_fixed_mana_ability_registry().validate({**descriptor,**delta})


class PublicQuantityManaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-quantity-mana-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mana amount witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve

    def test_quantity_mana_spell_counts_at_resolution_and_replays(self):
        session=self.session(283004);engine=session.engine
        self.add(engine,'Generic Bound Body')
        self.add(engine,'Generic Bound Body',seat='B',ref='other-body')
        spell=self.add(engine,'Battle Hymn',zone='hand')
        later=self.add(engine,'Generic Public Mana Flash Body',zone='hand')
        action=self.ready(session,spell,{'R':1,'C':1})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(0,session.state.players['A'].mana_pool['R'])
        # A real intervening cast makes a creature enter after the mana spell
        # was announced. The count must be sampled at the later instruction.
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        flash=next(row for row in actions if row.get('card')==later.ref)
        cast=session.act('pilot:A',{'action_id':flash['id'],'pay':'auto'})
        self.assertTrue(cast.ok,cast.summary)
        self.resolve(session)
        self.assertEqual(2,session.state.players['A'].mana_pool['R'])
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.replay(session,load=True)

    def test_quantity_mana_other_gate_excludes_source_and_pays_mana_price(self):
        session=self.session(283013);engine=session.engine
        source=self.add(engine,"Baldur's Gate")
        self.add(engine,'Azorius Guildgate');self.add(engine,'Rakdos Guildgate')
        self.add(engine,'Azorius Guildgate',seat='B',ref='enemy-gate')
        self.ready(session,source,{'C':2})
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==source.ref and row.get('choice_schema'))
        self.assertEqual(2,action['choice_schema']['mana_output']['total'])
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','mana_choice':'R'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual((2,0),(session.state.players['A'].mana_pool['R'],session.state.players['A'].mana_pool['C']))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session,load=True)

    def test_quantity_mana_controller_query_is_offered_accepted_and_replayed(self):
        session=self.session(283001);engine=session.engine
        source=self.add(engine,'Circle of Dreams Druid',seat='B')
        engine.change_control(source.object_id,'A',reason='Mana activating controller')
        source.acquired_control_turn_count=-1
        self.add(engine,'Generic Bound Body')
        self.add(engine,'Generic Bound Body',seat='B',ref='enemy-body')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-c')
        action=self.ready(session,source,{})
        self.assertTrue(action.get('mana_ability'))
        self.assertNotIn(private.ref,str(session.packet('pilot:B',full=True)))
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id']})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['G'])
        self.assertEqual(0,session.state.players['B'].mana_pool['G'])
        self.assertEqual([],session.state.stack)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session,load=True)

    def test_quantity_mana_source_cost_uses_postcost_and_predeparture_values(self):
        session=self.session(283002);engine=session.engine
        source=self.add(engine,'Lotus Blossom')
        source.counters['petal']=3
        action=self.ready(session,source,{})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'mana_choice':'U'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(3,session.state.players['A'].mana_pool['U'])
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual({},session.state.cards[source.object_id].counters)
        self.replay(session,load=True)

    def test_quantity_mana_counter_cost_is_removed_before_counting(self):
        session=self.session(283005);engine=session.engine
        source=self.add(engine,'Generic Public Counter Mana');source.counters['charge']=3
        action=self.ready(session,source,{})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,session.state.cards[source.object_id].counters['charge'])
        self.assertEqual(2,session.state.players['A'].mana_pool['C'])
        self.replay(session,load=True)

    def test_quantity_mana_mixed_allocation_and_zero_output_are_legal(self):
        session=self.session(283006);engine=session.engine
        source=self.add(engine,'Generic Public Mixed Mana')
        for index in range(3):self.add(engine,'Generic Bound Body',ref=f'own-{index}')
        action=self.ready(session,source,{})
        choice=action['choice_schema']['mana_output']
        self.assertEqual(['G','U'],choice['allowed_colors']);self.assertEqual(3,choice['total'])
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'mana_output':{'R':3}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'mana_output':{'G':1,'U':2}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual((1,2),(session.state.players['A'].mana_pool['G'],session.state.players['A'].mana_pool['U']))
        self.replay(session,load=True)
        empty=self.session(283007);source=self.add(empty.engine,'Generic Public Mixed Mana')
        action=self.ready(empty,source,{});self.checkpoint(empty)
        accepted=empty.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(empty.state.cards[source.object_id].tapped)
        self.assertEqual(0,sum(empty.state.players['A'].mana_pool.values()))
        self.replay(empty,load=True)

    def test_quantity_mana_graveyard_count_includes_paid_source_sacrifice(self):
        session=self.session(283008);engine=session.engine
        source=self.add(engine,'Generic Public Grave Mana')
        self.add(engine,'Generic Bound Body',zone='graveyard')
        action=self.ready(session,source,{})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['B'])
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_quantity_mana_cleared_source_mutant_is_killed(self):
        from quorune import mana_activation
        original=mana_activation.mana_output_for_ability
        def cleared(host,seat,source,ability,response,**kwargs):
            return original(host,seat,source,ability,response)
        with patch.object(mana_activation,'mana_output_for_ability',side_effect=cleared):
            with self.assertRaises(AssertionError):
                self.test_quantity_mana_source_cost_uses_postcost_and_predeparture_values()

    def test_quantity_mana_unknown_counter_is_rejected_but_known_zero_pays(self):
        session=self.session(283009);engine=session.engine
        source=self.add(engine,'Lotus Blossom');source.counters['petal']=None
        ability=next(row for row in engine._activated_abilities(source) if row.dynamic_mana_output is not None)
        before=authoritative_state_hash(session.state)
        from quorune.errors import GameRuleError
        with self.assertRaises(GameRuleError):engine._mana_modes_for_ability('A',source,ability)
        self.assertEqual(before,authoritative_state_hash(session.state))
        source.counters.clear()
        action=self.ready(session,source,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_quantity_mana_current_source_power_and_restricted_provenance(self):
        session=self.session(283010);engine=session.engine
        source=self.add(engine,'Viridian Joiner');source.counters['+1/+1']=2
        source.acquired_control_turn_count=-1
        action=self.ready(session,source,{});self.checkpoint(session)
        self.assertEqual(3,engine._numeric_stat(source.object_id,'power'))
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(3,session.state.players['A'].mana_pool['G'])
        self.replay(session,load=True)
        restricted=self.session(283011);engine=restricted.engine
        source=self.add(engine,'Generic Public Restricted Mana');self.add(engine,'Generic Bound Body')
        action=self.ready(restricted,source,{});self.checkpoint(restricted)
        accepted=restricted.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,restricted.state.players['A'].mana_pool['G'])
        ability=next(row for row in engine._activated_abilities(source) if row.dynamic_mana_output is not None)
        self.assertIsNotNone(ability.mana_spend_restriction)
        self.assertIn(ability.mana_spend_restriction,str(restricted.state.players['A'].stats))
        self.replay(restricted,load=True)

    def test_quantity_mana_pure_tap_producer_pays_real_cast(self):
        session=self.session(283012);engine=session.engine
        source=self.add(engine,'Circle of Dreams Druid');source.acquired_control_turn_count=-1
        spell=self.add(engine,'Generic Bound Growth',zone='hand');target=self.add(engine,'Generic Bound Body')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.resolve(session)
        self.replay(session,load=True)

    def test_quantity_mana_negative_source_power_and_history_use_existing_producers(self):
        session=self.session(283014);engine=session.engine
        source=self.add(engine,'Generic Public Mana Negative Power');source.acquired_control_turn_count=-1
        action=self.ready(session,source,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.replay(session,load=True)
        history=self.session(283015);engine=history.engine
        spell=self.add(engine,'Generic Public Mana History',zone='hand')
        action=self.ready(history,spell,{});self.checkpoint(history)
        accepted=history.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(history)
        self.assertEqual(43,history.state.players['A'].life)
        self.assertEqual(3,history.state.players['A'].mana_pool['G'])
        self.replay(history,load=True)

    def test_quantity_mana_invalid_allocation_rolls_back(self):
        session=self.session(283003);engine=session.engine
        source=self.add(engine,'Lotus Blossom');source.counters['petal']=3
        action=self.ready(session,source,{})
        self.checkpoint(session)
        for bundle in ({'U':4},{'U':1,'G':2},{'C':3},{'U':True},{'U':-3}):
            before=authoritative_state_hash(session.state)
            rejected=session.act('pilot:A',{'action_id':action['id'],'mana_output':bundle})
            self.assertFalse(rejected.ok,rejected.summary)
            self.assertEqual(before,authoritative_state_hash(session.state))
        source=session.state.cards[source.object_id]
        self.assertEqual('battlefield',source.zone);self.assertFalse(source.tapped)
        self.assertEqual(3,source.counters['petal'])


if __name__=='__main__':
    unittest.main()
