"""Receiver qualification stays live across crew, type layers and attachments."""
from copy import deepcopy
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as recipients
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.continuous_effect_model import ContinuousEffectRelation, ContinuousObjectIdentity, Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler
from quorune.compiler.qualified_attached_settings import (
    attached_characteristics_handler, qualified_attached_characteristics_handler,
)
from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
from quorune.semantic_runtime.qualified_attached_settings import (
    HANDLER, CAPABILITY, CREATURE_QUERY, VEHICLE_QUERY, QualifiedAttachedCharacteristicsHandler,
)
from scripts.build_test_database import build_fixture_database

VEHICLE_TEXT = "As long as enchanted permanent is a Vehicle, it's a creature in addition to its other types."
STAT_TEXT = 'As long as enchanted permanent is a creature, it gets -3/-0 and loses flying.'
BASE_TEXT = 'As long as enchanted permanent is a creature, it has base power and toughness 1/1.'
QUANTITY_TEXT = ('As long as enchanted permanent is a creature, it gets +1/+1 '
                 'for each creature and/or Vehicle you control.')
NAMES = ('Aerial Modification', 'Siege Modification', 'Short Circuit', 'Awakened Awareness', 'Born to Drive')


class QualifiedAttachedSettingModelTests(unittest.TestCase):
    def context(self, **extra):
        values = dict(source_object_id='aura', source_ref='AURA', source_controller='A', source_timestamp=7,
            component_id='qualified', source_logical_object_id='aura-incarnation',
            attached_object=ContinuousObjectIdentity('vehicle', 'original-vehicle'), resolved_quantity=3)
        return ContinuousEffectSourceContext(**{**values, **extra})

    def test_four_closed_bodies_keep_their_current_receiver_and_existing_modifier(self):
        for text, query in [(VEHICLE_TEXT, VEHICLE_QUERY), (STAT_TEXT, CREATURE_QUERY),
                            (BASE_TEXT, CREATURE_QUERY), (QUANTITY_TEXT, CREATURE_QUERY)]:
            with self.subTest(text=text):
                compiled = qualified_attached_characteristics_handler(text)
                self.assertIsNotNone(compiled)
                descriptor = compiled[1]
                self.assertEqual(HANDLER, descriptor['handler_id'])
                self.assertEqual(query, QualifiedAttachedCharacteristicsHandler().validate(descriptor)[0])
                self.assertIn(CAPABILITY, compiled[2])
                self.assertEqual(descriptor, deepcopy(descriptor))
        quantity = qualified_attached_characteristics_handler(QUANTITY_TEXT)[1]['modifier']['quantity']
        self.assertEqual('controller_zone', quantity['scope'])
        self.assertEqual(2, len(quantity['query']['any_of']))

    def test_lowering_keeps_logical_attachment_and_predicate_on_each_emitted_layer(self):
        for text, layers in [(VEHICLE_TEXT, {Layer.TYPE}), (STAT_TEXT, {Layer.ABILITY, Layer.POWER_TOUGHNESS}),
                             (BASE_TEXT, {Layer.POWER_TOUGHNESS}), (QUANTITY_TEXT, {Layer.POWER_TOUGHNESS})]:
            descriptor = qualified_attached_characteristics_handler(text)[1]
            before = deepcopy(descriptor)
            handler = QualifiedAttachedCharacteristicsHandler()
            effects = handler.lower(descriptor, self.context())
            self.assertEqual(layers, {e.layer for e in effects})
            self.assertEqual(before, descriptor)
            for effect in effects:
                self.assertEqual(ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT, effect.relation)
                self.assertEqual(self.context().attached_object, effect.related_object)
                self.assertEqual(ObjectQuerySpec.from_dict(descriptor['receiver_query']), effect.applies)
            self.assertEqual((), handler.lower(descriptor, self.context(attached_object=None)))
        self.assertEqual(Layer.TYPE, handler.descriptor_source_component_layer(
            qualified_attached_characteristics_handler(VEHICLE_TEXT)[1]))

    def test_closed_identity_query_version_and_modifier_boundaries_reject(self):
        base = qualified_attached_characteristics_handler(STAT_TEXT)[1]
        handler = QualifiedAttachedCharacteristicsHandler()
        for changes in ({'schema_version': True}, {'schema_version': 1.0}, {'schema_version': 2},
                {'event': 'resolve'}, {'extra': None},
                {'receiver_query': ObjectQuerySpec(zones=('hand',)).to_dict()},
                {'receiver_query': ObjectQuerySpec(zones=('battlefield',)).to_dict()},
                {'receiver_query': ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), controller='A').to_dict()}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                handler.validate({**base, **changes})
        vehicle = qualified_attached_characteristics_handler(VEHICLE_TEXT)[1]
        for modifier in ({**vehicle['modifier'], 'type_operations': []},
                         {**vehicle['modifier'], 'base_power': 0, 'base_toughness': 0},
                         {**base['modifier'], 'type_operations': vehicle['modifier']['type_operations']}):
            with self.subTest(modifier=modifier), self.assertRaises(ValueError):
                handler.validate({**vehicle, 'modifier': modifier})

    def test_unrepresented_conditions_quoted_riders_and_self_changing_qualification_reject(self):
        for text in ('As long as enchanted permanent is red, it gets +1/+1.',
                'As long as enchanted permanent is a Vehicle, it gets +1/+1.',
                'As long as enchanted permanent is a creature, it is an artifact in addition to its other types.',
                'As long as enchanted permanent is a creature, it has "Draw a card."',
                'As long as enchanted permanent is a creature, it gets +X/+X.',
                STAT_TEXT+' Draw a card.', STAT_TEXT.replace('As long as', 'If')):
            with self.subTest(text=text):self.assertIsNone(qualified_attached_characteristics_handler(text))

    def test_hidden_or_opponent_quantity_cannot_expand_the_receiver_family(self):
        from quorune.characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope
        descriptor = qualified_attached_characteristics_handler(QUANTITY_TEXT)[1]
        for quantity in (CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
                            query=ObjectQuerySpec(zones=('hand',))),
                         CharacteristicQuantitySpec(CharacteristicQuantityScope.OPPONENT_ZONES,
                            query=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)))):
            changed = {**descriptor, 'modifier': {**descriptor['modifier'], 'quantity': quantity.to_dict()}}
            with self.assertRaises(ValueError):QualifiedAttachedCharacteristicsHandler().validate(changed)

    def test_legacy_unqualified_descriptors_are_identical_and_leaf_rejections_preserved(self):
        text = 'Enchanted creature gets +2/+2 and has flying.'
        self.assertEqual(attached_fixed_characteristics_handler(text), attached_characteristics_handler(text))
        self.assertIsNone(attached_fixed_characteristics_handler(STAT_TEXT))
        self.assertIsNotNone(attached_characteristics_handler(STAT_TEXT))


class QualifiedAttachedSettingNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish
    finish_turn = recipients.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'qualified-attachment.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/qualified-attached-setting-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Qualified attachment settings', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def creature(self, session, card):
        return 'creature' in session.engine._type_parts(
            session.engine._effective_card_data(session.state.cards[card.object_id])['type_line'])[0]

    def stats(self, session, card):
        return tuple(session.engine._numeric_stat(card.object_id, stat) for stat in ('power','toughness'))

    def keywords(self, session, card):
        return {str(k).casefold() for k in session.engine._combat_keywords(session.state.cards[card.object_id])}

    def crew(self, session, vehicle, crewman):
        self.priority(session,'A')
        rows = session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered, = [r for r in rows if r['id'].startswith('activate:'+vehicle.ref+':')
                    and any(c['k']=='crew' for c in r.get('cost_summary',{}).get('choose_cost',[]))]
        command = {'action_id': offered['id'], 'cost_cards': [crewman.ref], 'pay': 'auto'}
        self.reject(session,'B',command)
        result = session.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        total_power = session.state.stack[-1].context['crew']['total_power']
        self.assertIsNone(self.resolve(session))
        return total_power

    def test_all_five_originals_have_complete_normal_source_and_ability_admission(self):
        session = self.session(6134301)
        for name in NAMES:
            source = self.add(session.engine,name,zone='hand')
            self.assert_closed(session,source)
            ir = compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,
                                     capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_actual_modifications_animate_without_crew_and_disappear_with_the_aura(self):
        for index,(name,stats,keyword) in enumerate([
                ('Aerial Modification',(4,5),'flying'),('Siege Modification',(5,3),'first strike')]):
            session = self.session(6134310+index)
            aura = self.add(session.engine,name,zone='hand')
            vehicle = self.add(session.engine,'Sky Skiff')
            removal = self.add(session.engine,'Disenchant',seat='B',zone='hand')
            private = self.add(session.engine,'Counterspell',seat='C',zone='hand')
            for card in (aura,vehicle,removal):self.assert_closed(session,card)
            self.prepare(session,mana={'C':10,'W':2,'R':2})
            session.state.players['B'].mana_pool.update({'C':1,'W':1})
            self.checkpoint(session)
            self.assertFalse(self.creature(session,vehicle))
            self.cast(session,'A',aura,[vehicle.ref])
            session = self.replay(session,load=True)
            self.assertIsNone(self.resolve(session))
            self.assertTrue(self.creature(session,vehicle))
            self.assertEqual(stats,self.stats(session,vehicle))
            self.assertIn(keyword,self.keywords(session,vehicle))
            self.cast(session,'B',removal,[session.state.cards[aura.object_id].ref])
            self.assertIsNone(self.resolve(session))
            self.assertFalse(self.creature(session,vehicle))
            self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
            self.finish(session,private)

    def test_actual_short_circuit_flash_and_live_crew_then_cleanup_qualification(self):
        session = self.session(6134320)
        aura = self.add(session.engine,'Short Circuit',zone='hand')
        vehicle = self.add(session.engine,'Sky Skiff')
        crewman = self.add(session.engine,'Memnite')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand')
        for card in (aura,vehicle,crewman):self.assert_closed(session,card)
        self.prepare(session,active='B',mana={'C':1,'U':1})
        self.checkpoint(session)
        self.cast(session,'A',aura,[vehicle.ref])
        self.assertIsNone(self.resolve(session))
        self.assertFalse(self.creature(session,vehicle))
        self.assertIn('flying',self.keywords(session,vehicle))
        self.crew(session,vehicle,crewman)
        self.assertTrue(self.creature(session,vehicle))
        self.assertEqual((-1,3),self.stats(session,vehicle))
        self.assertNotIn('flying',self.keywords(session,vehicle))
        self.finish_turn(session)
        self.assertFalse(self.creature(session,vehicle))
        self.assertIn('flying',self.keywords(session,vehicle))
        self.assertEqual(vehicle.object_id,session.state.cards[aura.object_id].attached_to)
        self.finish(session,private)

    def test_actual_awareness_pays_x_and_orders_base_stats_before_entry_trigger_counters(self):
        session = self.session(6134330)
        aura = self.add(session.engine,'Awakened Awareness',zone='hand')
        vehicle = self.add(session.engine,'Sky Skiff')
        crewman = self.add(session.engine,'Memnite')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand')
        for card in (aura,vehicle,crewman):self.assert_closed(session,card)
        self.prepare(session,mana={'C':3,'U':2})
        self.checkpoint(session)
        offered = self.action(session,'A',aura)
        command = {'action_id':offered['id'],'targets':[vehicle.ref],'x':3,'pay':'auto'}
        self.reject(session,'B',command)
        result = session.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        session = self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3,session.state.cards[vehicle.object_id].counters.get('+1/+1',0))
        self.assertFalse(self.creature(session,vehicle))
        self.crew(session,vehicle,crewman)
        self.assertEqual((4,4),self.stats(session,vehicle))
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        self.finish(session,private)

    def test_actual_born_counts_creature_vehicle_union_once_and_current_aura_controller(self):
        session = self.session(6134340)
        aura = self.add(session.engine,'Born to Drive',zone='hand')
        vehicle = self.add(session.engine,'Sky Skiff')
        crewman = self.add(session.engine,'Memnite')
        donate = self.add(session.engine,'Donate',zone='hand')
        for i in range(3):self.add(session.engine,'Memnite',seat='B',ref='opposing-memnite-'+str(i))
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand')
        for card in (aura,vehicle,crewman,donate):self.assert_closed(session,card)
        self.prepare(session,mana={'C':8,'W':1,'U':1})
        self.checkpoint(session)
        self.cast_for(session,'A',aura,[vehicle.ref],reserve={'U':1})
        self.assertIsNone(self.resolve(session))
        self.assertFalse(self.creature(session,vehicle))
        self.crew(session,vehicle,crewman)
        self.assertEqual((4,5),self.stats(session,vehicle))
        self.cast(session,'A',donate,['B',session.state.cards[aura.object_id].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('B',session.state.cards[aura.object_id].controller)
        self.assertEqual((5,6),self.stats(session,vehicle))
        self.finish(session,private)

    def test_actual_channel_discards_and_creates_both_pilots_with_their_crew_bonus(self):
        session = self.session(6134350)
        source = self.add(session.engine,'Born to Drive',zone='hand')
        vehicle = self.add(session.engine,'Renegade Freighter')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand')
        for card in (source,vehicle):self.assert_closed(session,card)
        self.prepare(session,mana={'C':2,'W':1})
        self.checkpoint(session)
        rows = session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered, = [r for r in rows if r['id'].startswith('activate:'+source.ref+':')]
        command = {'action_id':offered['id'],'targets':[],'pay':'auto'}
        self.reject(session,'B',command)
        result = session.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        pilots = [c for c in session.state.cards.values() if c.zone=='battlefield' and c.controller=='A'
                  and c.printed_name=='Pilot Token' and c.is_token]
        self.assertEqual(2,len(pilots))
        for pilot in pilots:
            data = session.engine._effective_card_data(pilot)
            self.assertEqual((1,1),self.stats(session,pilot))
            self.assertFalse(data.get('colors'))
            types,subtypes,_ = session.engine._type_parts(data['type_line'])
            self.assertIn('creature',types)
            self.assertIn('pilot',subtypes)
        for count,pilot in enumerate(pilots,1):
            self.assertEqual(3,self.crew(session,vehicle,pilot))
            self.assertEqual(count,sum(session.state.cards[c.object_id].tapped for c in pilots))
        self.assertTrue(self.creature(session,vehicle))
        self.finish(session,private)

    def test_payload_minimum_requires_qualified_component_without_coverage(self):
        session = self.session(6134360)
        aura = self.add(session.engine,'Aerial Modification',zone='hand')
        self.assert_closed(session,aura)
        program, = [p for p in session.engine.semantics.programs_for_oracle(aura.oracle_id)
                    if any(h.get('handler_id')==HANDLER for h in p.handlers)]
        deps = tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
        stripped = replace(program,coverage=(),capability_dependencies=deps,
            capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])

    def test_fresh_receiver_predicate_omission_is_killed_by_actual_uncrewed_vehicle(self):
        original = QualifiedAttachedCharacteristicsHandler.lower
        def omit(handler,descriptor,context):
            return tuple(replace(effect,applies=ObjectQuerySpec(zones=('battlefield',)))
                         for effect in original(handler,descriptor,context))
        method = 'test_actual_short_circuit_flash_and_live_crew_then_cleanup_qualification'
        with patch.object(QualifiedAttachedCharacteristicsHandler,'lower',omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
