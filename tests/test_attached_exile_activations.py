from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.attachment_references import AttachmentReferenceKind as K, AttachmentReferenceSpec
from quorune.compiler.exile_templates import attached_exile_effect_template
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import targeted_exile_node_capabilities
from test_qualified_zone_event_queries import query_record
from scripts.build_test_database import build_fixture_database
import test_life_gain_prohibitions as native


class AttachedExileActivationCompilerTests(unittest.TestCase):
    def test_attached_keyword_grant_preserves_the_permanent_and_exact_keyword_owners(self):
        from quorune.compiler.fixed_resolution_characteristics import fixed_attached_keyword_effect_template
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies
        from quorune.rules.node_capability_shapes import fixed_source_characteristics_node_capabilities

        text = 'Enchanted creature gains trample and indestructible until end of turn.'
        _, effects, schema, mechanics = fixed_attached_keyword_effect_template(text, source_attachment_relation=K.ENCHANTED)
        effect, = effects
        self.assertIsNone(schema)
        self.assertEqual(AttachmentReferenceSpec(K.ENCHANTED, 'permanent', schema_version=2).to_dict(), effect['card'])
        self.assertEqual(['Trample', 'Indestructible'], effect['keywords'])
        dependencies = fixed_source_characteristics_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics)
        self.assertTrue({'object.reference.source_attachment_information', 'combat.damage.assignment.trample',
                         'permanent.indestructible.ordinary'} <= set(dependencies))
        self.assertEqual(dependencies, fixed_resolution_characteristics_runtime_dependencies(
            SimpleNamespace(effects=effects, target_schema=None, coverage=()),
        ))
        for forged in ({**effect, 'card': {**effect['card'], 'schema_version': 1}},
                       {**effect, 'keywords': ['Trample', 'Trample']}, {**effect, 'power': False}):
            self.assertEqual((), fixed_source_characteristics_node_capabilities(
                effects=(forged,), target_schema=None, mechanic_ids=mechanics,
            ))
        self.assertIsNone(fixed_attached_keyword_effect_template(text, source_attachment_relation=None))
        self.assertIsNone(fixed_attached_keyword_effect_template(text + ' Draw a card.', source_attachment_relation=K.ENCHANTED))

    def test_fixed_attached_stats_require_the_closed_result_and_versioned_reference(self):
        from quorune.compiler.fixed_resolution_characteristics import fixed_attached_stat_effect_template
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies
        from quorune.rules.node_capability_shapes import fixed_source_characteristics_node_capabilities

        text = 'Enchanted creature gets +2/-2 until end of turn.'
        compiled = fixed_attached_stat_effect_template(text, source_attachment_relation=K.ENCHANTED)
        self.assertIsNotNone(compiled)
        _, effects, target_schema, mechanics = compiled
        self.assertIsNone(target_schema)
        effect, = effects
        reference = AttachmentReferenceSpec(K.ENCHANTED, 'permanent', schema_version=2).to_dict()
        self.assertEqual({'op': 'modify_stats_until_end_of_turn', 'card': reference, 'power': 2, 'toughness': -2}, effect)
        required = {'continuous.resolution.fixed_characteristics_until_end_of_turn',
                    'object.reference.source_attachment_information'}
        self.assertEqual(required, set(fixed_source_characteristics_node_capabilities(
            effects=effects, target_schema=None, mechanic_ids=mechanics)))
        self.assertEqual(required, set(fixed_resolution_characteristics_runtime_dependencies(
            SimpleNamespace(effects=effects, target_schema=None, coverage=()))))
        for bad in (
            {**effect, 'card': {**reference, 'schema_version': 1}},
            {**effect, 'card': {**reference, 'required_card_type': 'creature'}},
            {**effect, 'card': {**reference, 'relation': K.EQUIPPED.value}},
            {**effect, 'power': True},
            {**effect, 'power': 0, 'toughness': 0},
        ):
            with self.subTest(bad=bad):
                self.assertEqual((), fixed_source_characteristics_node_capabilities(
                    effects=(bad,), target_schema=None, mechanic_ids=mechanics))
                with self.assertRaises(ValueError):
                    fixed_resolution_characteristics_runtime_dependencies(
                        SimpleNamespace(effects=(bad,), target_schema=None, coverage=()))
        with self.assertRaises(ValueError):
            fixed_resolution_characteristics_runtime_dependencies(
                SimpleNamespace(effects=effects, target_schema={'count': 1}, coverage=()))
        for relation in (None, K.EQUIPPED):
            self.assertIsNone(fixed_attached_stat_effect_template(text, source_attachment_relation=relation))
        for unsupported in (
            'Enchanted creature gets +0/+0 until end of turn.',
            'Enchanted creature gets +X/+0 until end of turn.',
            'Enchanted creature gets +1/+0 until your next turn.',
            'Enchanted creature gets +1/+0 until end of turn if it blocked.',
        ):
            self.assertIsNone(fixed_attached_stat_effect_template(unsupported, source_attachment_relation=K.ENCHANTED))

    def test_attached_exile_requires_the_source_relation_and_versioned_information(self):
        template = attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=K.ENCHANTED)
        self.assertIsNotNone(template)
        identity,effects,schema,mechanics = template.compiled()
        self.assertEqual('exile-source-attachment-v1',identity)
        self.assertIsNone(schema)
        self.assertEqual(('exile',),mechanics)
        reference = AttachmentReferenceSpec(K.ENCHANTED,'permanent',schema_version=2)
        self.assertEqual(({'op':'exile_permanent','card':reference.to_dict()},),effects)
        required = targeted_exile_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics)
        self.assertEqual(('permanent.exile.effect','object.reference.source_attachment_information'),required)
        self.assertNotIn('target.revalidate_resolution',required)
        for relation in (None,K.EQUIPPED,K.FORTIFIED):
            self.assertIsNone(attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=relation))
        for text in ('Exile enchanted permanent.','Exile equipped creature.',
                'You may exile enchanted creature.','Exile enchanted creature until this Aura leaves the battlefield.',
                'Exile enchanted creature. Draw a card.'):
            self.assertIsNone(attached_exile_effect_template(text,source_attachment_relation=K.ENCHANTED))

    def test_attached_exile_reference_and_target_shape_mutants_fail_closed(self):
        effect = attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=K.ENCHANTED).compiled()[1][0]
        for fields in ({'schema_version':1},{'schema_version':True},{'schema_version':2.0},
                {'required_card_type':'creature'},{'relation':'equipped_object'},{'unknown':True}):
            changed = {**effect,'card':{**effect['card'],**fields}}
            self.assertFalse(targeted_exile_node_capabilities(effects=(changed,),target_schema=None,mechanic_ids=('exile',)))
        for effects,schema,mechanics in ((({**effect,'unknown':True},),None,('exile',)),
                ((effect,effect),None,('exile',)),((effect,),{'count':1},('exile',)),
                ((effect,),None,('exile','cr-115-targets')),((effect,),None,())):
            self.assertFalse(targeted_exile_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))

    def test_attached_exile_compiles_a_complete_source_with_its_original_activation_cost(self):
        record = replace(query_record('Enchant creature\n{2}{W}: Exile enchanted creature.'),
            name='Generic Exile Aura',type_line='Enchantment — Aura',power=None,toughness=None,keywords=('Enchant',))
        ir = compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node = next(node for node in ir.faces[0].nodes if node.template_id=='exile-source-attachment-v1')
        self.assertEqual('activate',node.event)
        self.assertIsNone(node.target_schema)
        self.assertEqual({'GENERIC':2,'W':1,'U':0,'B':0,'R':0,'G':0,'C':0},node.cost['mana'])
        self.assertIn('object.reference.source_attachment_information',node.capability_dependencies)

    def test_omitting_attached_exile_lowering_is_detected(self):
        method = 'test_attached_exile_compiles_a_complete_source_with_its_original_activation_cost'
        with patch('quorune.compiler.exile_templates.attached_exile_effect_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_compiled_exile_reference_tracks_current_and_departure_information(self):
        from quorune.model import CardInstance
        from quorune.attachments import attach_objects
        from quorune.attachment_references import ATTACHMENT_INFORMATION_VERSION, capture_source_attachment_snapshot
        from quorune.attachment_information import pin_attachment_information_departures
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.characteristic_evaluation import type_parts
        cards = {key:CardInstance(object_id=key,ref=key,oracle_id='model:'+key,printed_name=key,
            owner='A',controller='A',zone='battlefield') for key in ('aura','first','second')}
        aura,first,second = (cards[key] for key in ('aura','first','second'))
        attach_objects(cards,aura,first,source_timestamp=1)
        context = dict(source_attachment_snapshot=capture_source_attachment_snapshot(cards,aura,K.ENCHANTED).to_dict(),
            source_logical_object_id=aura.logical_object_id)
        current = SimpleNamespace(source_object_id=aura.object_id,card_object_id=None,
            context={**context,ATTACHMENT_INFORMATION_VERSION:1})
        legacy = SimpleNamespace(source_object_id=aura.object_id,card_object_id=None,context=dict(context))
        host = SimpleNamespace(state=SimpleNamespace(cards=cards,stack=[current,legacy]),
            _type_parts=type_parts,_target_snapshot=lambda ref:{'type_line':'Creature'})
        reference = attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=K.ENCHANTED).compiled()[1][0]['card']
        attach_objects(cards,aura,second,source_timestamp=2)
        self.assertEqual(second.ref,resolve_semantic_value(host,reference,current))
        pin_attachment_information_departures(host,[aura])
        aura.zone = 'graveyard'
        aura.zone_change_counter += 1
        self.assertEqual(second.ref,resolve_semantic_value(host,reference,current))
        self.assertEqual(first.ref,resolve_semantic_value(host,{**reference,'schema_version':1},legacy))
        second.zone_change_counter += 1
        self.assertIsNone(resolve_semantic_value(host,reference,current))


NAMES = ('Cooped Up','Choking Restraints','Utter Insignificance','Dreadful Apathy')


class AttachedExileActivationRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LifeGainProhibitionRuntimeTests.session
    add = native.LifeGainProhibitionRuntimeTests.add
    prepare = native.LifeGainProhibitionRuntimeTests.prepare
    checkpoint = native.LifeGainProhibitionRuntimeTests.checkpoint
    resolve = native.LifeGainProhibitionRuntimeTests.resolve
    replay = native.LifeGainProhibitionRuntimeTests.replay
    private_replay = native.LifeGainProhibitionRuntimeTests.private_replay
    priority = native.LifeGainProhibitionRuntimeTests.priority
    actions = native.LifeGainProhibitionRuntimeTests.actions
    cast = native.LifeGainProhibitionRuntimeTests.cast
    reject = native.LifeGainProhibitionRuntimeTests.reject
    binding = native.LifeGainProhibitionRuntimeTests.binding
    activation_command = native.LifeGainProhibitionRuntimeTests.activation_command
    activate = native.LifeGainProhibitionRuntimeTests.activate
    resume = native.LifeGainProhibitionRuntimeTests.resume
    settle = native.LifeGainProhibitionRuntimeTests.settle
    resolve_top = InheritedNativeFixture.resolve_top
    until = InheritedNativeFixture.until

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'attached-exile.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/attached-exile-activation-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source Aura exile',[
            DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def fixture(self, seed, *, name='Cooped Up', recipient_seat='B'):
        s = self.session(seed)
        aura = self.add(s.engine,name,zone='hand')
        recipient = self.add(s.engine,'Wind Drake',seat=recipient_seat)
        recipient.acquired_control_turn_count = -1
        other = self.add(s.engine,'Fugitive Wizard',ref='other-recipient')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        return s,aura,recipient,other,private

    def cast_aura(self, s, aura, recipient):
        if aura.printed_name == 'Utter Insignificance':payment = {'C':1,'U':1}
        else:payment = {'C':1 if aura.printed_name == 'Cooped Up' else 2,'W':1}
        self.assertTrue(self.binding(s,aura)['strict_capability_ready'])
        self.cast(s,aura,[recipient.ref],payment=payment)
        self.assertIsNone(self.settle(s))
        self.assertEqual(recipient.object_id,s.state.cards[aura.object_id].attached_to)

    def stats(self, session, card):
        return tuple(session.engine._numeric_stat(card.object_id, stat) for stat in ('power', 'toughness'))

    def test_fae_flight_grants_hexproof_to_the_last_same_incarnation(self):
        from quorune.attachment_references import ATTACHMENT_INFORMATION_VERSION

        cases = (
            ('source departs', 'Disenchant', 'A', {'C': 1, 'W': 1}),
            ('creature returns', 'Cloudshift', 'B', {'W': 1}),
            ('creature changes type', 'Song of the Dryads', 'A', {'C': 2, 'G': 1}),
        )
        for index, (case, response_name, responder, payment) in enumerate(cases):
            with self.subTest(case=case):
                session, aura, body, other, private = self.fixture(113700180 + index, name='Fae Flight')
                response = self.add(session.engine, response_name, seat=responder, zone='hand')
                probe = self.add(session.engine, 'Humble', zone='hand', ref='attachment-target-probe')
                if case == 'creature changes type':
                    self.add(session.engine, 'Vedalken Orrery')
                self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
                self.prepare(session, mana={'C': 3, 'G': 2, 'U': 1, 'W': 2})
                session.state.players['B'].mana_pool['W'] = 1
                self.checkpoint(session)
                original = body.logical_object_id
                self.cast(session, aura, [body.ref], payment={'C': 1, 'U': 1})
                self.resolve_top(session)
                trigger, = session.state.stack
                self.assertFalse(trigger.targets)
                self.assertEqual(1, trigger.context[ATTACHMENT_INFORMATION_VERSION])
                target = aura if case == 'source departs' else body
                self.cast(session, response, [target.ref], seat=responder, payment=payment)
                self.resolve_top(session)
                self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
                self.assertIsNone(self.settle(session))
                current = session.state.cards[body.object_id]
                retained = case != 'creature returns'
                self.assertEqual(retained, current.logical_object_id == original)
                self.assertEqual(retained, 'hexproof' in session.engine._combat_keywords(current))
                if case == 'creature changes type':
                    effective = session.engine._effective_card_data(current)
                    self.assertNotIn('creature', session.engine._type_parts(effective['type_line'])[0])
                else:
                    self.priority(session, 'A')
                    offered = next(r for r in self.actions(session) if r['id'] == 'cast:' + probe.ref)
                    legal = offered['target_schema']['legal_refs']
                    self.assertIn(other.ref, legal)
                    self.assertEqual(not retained, current.ref in legal)
                    if retained:
                        self.reject(session, {'action_id': offered['id'], 'targets': [current.ref], 'pay': 'auto'})
                self.private_replay(session, private)

    def test_keldon_mantle_paid_activation_grants_trample_to_its_attachment(self):
        session, aura, body, other, private = self.fixture(113700183, name='Keldon Mantle')
        self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
        self.prepare(session, mana={'C': 1, 'R': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, aura, [body.ref], payment={'C': 1, 'R': 1})
        self.assertIsNone(self.settle(session))
        self.activate(session, aura)
        item, = session.state.stack
        self.assertFalse(item.targets)
        self.assertEqual(body.ref, item.context['source_attachment_snapshot']['attached_object']['reference'])
        self.assertEqual(0, session.state.players['A'].mana_pool.get('G', 0))
        self.assertIsNone(self.settle(session))
        self.assertIn('trample', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertNotIn('trample', session.engine._combat_keywords(session.state.cards[other.object_id]))
        self.private_replay(session, private)

    def test_ghitu_stats_follow_current_or_last_attachment_without_following_a_blinked_creature(self):
        from quorune.attachments import attach_objects
        from quorune.attachment_references import ATTACHMENT_INFORMATION_VERSION

        session = self.session(113700170)
        aura = self.add(session.engine, 'Ghitu Firebreathing', zone='hand', ref='stats-ghitu-aura')
        first = self.add(session.engine, 'Generic Bound Body', seat='B', ref='stats-original-creature')
        second = self.add(session.engine, 'Generic Bound Body', ref='stats-new-creature')
        blink = self.add(session.engine, 'Cloudshift', zone='hand', ref='stats-blink')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='stats-private-hand')
        self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
        self.prepare(session, mana={'C': 1, 'R': 3, 'W': 1})
        self.checkpoint(session)
        self.cast(session, aura, [first.ref], payment={'C': 1, 'R': 1})
        self.assertIsNone(self.settle(session))
        self.assertEqual(first.object_id, session.state.cards[aura.object_id].attached_to)

        command = self.activation_command(session, aura)
        self.reject(session, command, seat='B')
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        item, = session.state.stack
        self.assertFalse(item.targets)
        self.assertEqual(1, item.context[ATTACHMENT_INFORMATION_VERSION])
        self.assertEqual(first.ref, item.context['source_attachment_snapshot']['attached_object']['reference'])
        session = self.replay(session, load=True)

        # Reattachment is a prepared owner interaction. The paid actions and both
        # resolutions are ordinary player commands recorded from their checkpoints.
        provider = session.state.cards[aura.object_id]
        attach_objects(session.state.cards, provider, session.state.cards[second.object_id],
                       source_timestamp=provider.zone_timestamp, players=session.state.players)
        self.checkpoint(session)
        departed = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.assertEqual((2, 6), self.stats(session, first))
        self.assertEqual((3, 6), self.stats(session, second))
        self.private_replay(session, private)
        provider = session.state.cards[aura.object_id]
        attach_objects(session.state.cards, provider, session.state.cards[first.object_id],
                       source_timestamp=provider.zone_timestamp, players=session.state.players)
        self.assertEqual((2, 6), self.stats(session, first))
        self.assertEqual((3, 6), self.stats(session, second))

        self.priority(departed, 'A')
        bounce = next(row['id'] for row in self.actions(departed)
                      if row['id'].startswith('activate:' + aura.ref + ':') and row['id'] != command['action_id'])
        result = departed.act('pilot:A', {'action_id': bounce, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(departed)
        self.assertEqual('hand', departed.state.cards[aura.object_id].zone)
        item, = departed.state.stack
        self.assertEqual(second.ref, item.context['source_attachment_snapshot']['attached_object']['reference'])

        returned = self.replay(departed, load=True)
        self.assertIsNone(self.settle(departed))
        self.assertEqual((2, 6), self.stats(departed, first))
        self.assertEqual((3, 6), self.stats(departed, second))
        self.private_replay(departed, private)

        original = returned.state.cards[second.object_id].logical_object_id
        self.cast(returned, blink, [second.ref], payment={'W': 1})
        self.assertIsNone(self.settle(returned))
        self.assertNotEqual(original, returned.state.cards[second.object_id].logical_object_id)
        self.assertEqual((2, 6), self.stats(returned, first))
        self.assertEqual((2, 6), self.stats(returned, second))
        self.private_replay(returned, private)

    def test_briar_shield_creates_the_modifier_even_if_the_original_recipient_stops_being_a_creature(self):
        session = self.session(113700171)
        aura = self.add(session.engine, 'Briar Shield', zone='hand', ref='stats-sacrificed-aura')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='stats-type-changing-recipient')
        song = self.add(session.engine, 'Song of the Dryads', zone='hand', ref='stats-temporary-type-loss')
        disenchant = self.add(session.engine, 'Disenchant', zone='hand', ref='stats-type-restoration')
        self.add(session.engine, 'Vedalken Orrery')
        self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
        self.prepare(session, mana={'C': 3, 'G': 2, 'W': 1})
        self.checkpoint(session)
        self.cast(session, aura, [body.ref], payment={'G': 1})
        self.assertIsNone(self.settle(session))
        self.assertEqual((3, 7), self.stats(session, body))
        self.activate(session, aura)
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        self.assertFalse(session.state.stack[-1].targets)
        self.assertEqual((2, 6), self.stats(session, body))
        original = session.state.cards[body.object_id].logical_object_id

        self.cast(session, song, [body.ref], payment={'C': 2, 'G': 1})
        self.resolve_top(session)
        self.assertNotIn('creature', session.engine._type_parts(session.engine._effective_card_data(
            session.state.cards[body.object_id])['type_line'])[0])
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.cast(session, disenchant, [song.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.settle(session))
        self.assertEqual(original, session.state.cards[body.object_id].logical_object_id)
        self.assertEqual((5, 9), self.stats(session, body))
        self.replay(session, load=True)

    def test_flowstone_embrace_taps_its_own_source_and_expires_its_signed_result_at_cleanup(self):
        session = self.session(113700172)
        aura = self.add(session.engine, 'Flowstone Embrace', zone='hand', ref='stats-tap-aura')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='stats-tap-aura-recipient')
        self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)
        self.cast(session, aura, [body.ref], payment={'C': 1, 'R': 1})
        self.assertIsNone(self.settle(session))
        command = self.activation_command(session, aura)
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[aura.object_id].tapped)
        self.assertFalse(session.state.cards[body.object_id].tapped)
        self.reject(session, command)
        self.assertIsNone(self.settle(session))
        self.assertEqual((4, 4), self.stats(session, body))
        self.until(session, lambda game: game.state.active_player == 'B' and game.state.step == 'upkeep')
        self.assertEqual((2, 6), self.stats(session, body))
        self.replay(session, load=True)

    def test_paid_original_aura_exile_preserves_static_bodies_and_untargeted_shroud_behavior(self):
        for i,name in enumerate(NAMES):
            with self.subTest(name=name):
                s,aura,recipient,other,private = self.fixture(113700100+i,name=name)
                robe = self.add(s.engine,'Robe of Mirrors',zone='hand')
                self.prepare(s,mana={'C':12,'W':8,'U':4})
                self.checkpoint(s)
                self.cast_aura(s,aura,recipient)
                if name == 'Utter Insignificance':
                    self.assertEqual((1,1),(s.engine._numeric_stat(recipient.object_id,'power'),
                        s.engine._numeric_stat(recipient.object_id,'toughness')))
                    self.assertNotIn('flying',s.engine._combat_keywords(s.state.cards[recipient.object_id]))
                    self.assertIn('flash',s.engine._combat_keywords(s.state.cards[aura.object_id]))
                else:
                    self.assertFalse(s.engine._can_block(s.state.cards[other.object_id],
                        s.state.cards[recipient.object_id])[0])
                self.cast(s,robe,[recipient.ref],payment={'U':1})
                self.assertIsNone(self.settle(s))
                self.assertIn('shroud',s.engine._combat_keywords(s.state.cards[recipient.object_id]))
                command = self.activation_command(s,aura)
                programs = s.engine.semantics.programs_for_oracle(aura.oracle_id)
                activation = next(program for program in programs if program.event == 'activate')
                self.assertIsNone(activation.target_schema)
                self.reject(s,command,seat='D')
                result = s.act('pilot:A',command)
                self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard' if name == 'Choking Restraints' else 'battlefield',
                    s.state.cards[aura.object_id].zone)
                s = self.resume(s)
                self.assertIsNone(self.settle(s))
                self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
                self.assertIn(recipient.object_id,s.state.players['B'].zones['exile'])
                self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[robe.object_id].zone)
                self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
                self.private_replay(s,private)

    def test_actual_source_departure_keeps_original_attachment_and_replays_pending_ability(self):
        s,aura,recipient,other,private = self.fixture(113700120)
        destroy = self.add(s.engine,'Disenchant',zone='hand')
        self.prepare(s,mana={'C':8,'W':5})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        self.activate(s,aura)
        self.cast(s,destroy,[aura.ref],payment={'C':1,'W':1})
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.private_replay(s,private)

    def test_returned_aura_incarnation_does_not_replace_pending_source_information(self):
        s,aura,recipient,other,private = self.fixture(113700130)
        self.add(s.engine,'Vedalken Orrery')
        bounce = self.add(s.engine,'Boomerang',zone='hand')
        self.prepare(s,mana={'C':9,'W':5,'U':2})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        original_identity = s.state.cards[aura.object_id].logical_object_id
        self.activate(s,aura)
        self.cast(s,bounce,[aura.ref],payment={'U':2})
        # Resolve only the response; keep the older activated ability pending.
        while len(s.state.stack) > 1:
            result = s.act(s.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[aura.object_id].zone)
        self.assertNotEqual(original_identity,s.state.cards[aura.object_id].logical_object_id)
        self.cast(s,s.state.cards[aura.object_id],[other.ref],payment={'C':1,'W':1})
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[aura.object_id].zone)
        self.assertEqual(other.object_id,s.state.cards[aura.object_id].attached_to)
        self.private_replay(s,private)

    def test_actual_recipient_blink_never_exiles_the_returned_creature(self):
        s,aura,recipient,other,private = self.fixture(113700140,recipient_seat='A')
        blink = self.add(s.engine,'Cloudshift',zone='hand')
        self.prepare(s,mana={'C':8,'W':5})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        original_identity = s.state.cards[recipient.object_id].logical_object_id
        self.activate(s,aura)
        self.cast(s,blink,[recipient.ref],payment={'W':1})
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('battlefield',s.state.cards[recipient.object_id].zone)
        self.assertNotEqual(original_identity,s.state.cards[recipient.object_id].logical_object_id)
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.private_replay(s,private)

    def test_sacrificed_aura_exiles_its_same_recipient_after_actual_creature_type_loss(self):
        s,aura,recipient,other,private = self.fixture(113700150,name='Choking Restraints')
        self.add(s.engine,'Vedalken Orrery')
        change = self.add(s.engine,'Song of the Dryads',zone='hand')
        self.prepare(s,mana={'C':12,'W':8,'G':1})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        original_identity = s.state.cards[recipient.object_id].logical_object_id
        self.activate(s,aura)
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.cast(s,change,[recipient.ref],payment={'C':2,'G':1})
        while len(s.state.stack) > 1:
            result = s.act(s.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        current = s.state.cards[recipient.object_id]
        types = s.engine._type_parts(s.engine._effective_card_data(current)['type_line'])[0]
        self.assertIn('land',types)
        self.assertNotIn('creature',types)
        self.assertEqual(original_identity,current.logical_object_id)
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[change.object_id].zone)
        self.private_replay(s,private)

    def test_verified_attached_exile_authority_withdrawals_are_specific_rules_blocks(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        value = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for cap in ('permanent.exile.effect','object.reference.source_attachment_information'):
            changed = deepcopy(value)
            next(row for row in changed['capabilities'] if row['id']==cap).update(
                status='blocked',blockers=['withdrawn attached-exile authority'],dependency_fail_closed_status='not_run')
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in NAMES:
                ir = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                blockers = [blocker for residual in ir.material_residuals for blocker in residual.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in blocker for blocker in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in blocker for blocker in blockers),blockers)

    def test_attached_exile_cannot_omit_its_runtime_or_information_dependency(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,aura,recipient,other,private = self.fixture(113700160)
        program = next(program for program in s.engine.semantics.programs_for_oracle(aura.oracle_id)
            if program.event=='activate')
        for cap in ('permanent.exile.effect','object.reference.source_attachment_information'):
            dependencies = tuple(value for value in program.capability_dependencies if value != cap)
            missing = replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertTrue(any(cap in blocker for blocker in bound['blockers']),bound['blockers'])


if __name__ == '__main__':
    unittest.main()
