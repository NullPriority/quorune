from __future__ import annotations

"""CR 109.5/611.3/614.12a: retained choices and live conditions compose."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.entry_designation_templates import chosen_characteristics_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime import default_continuous_effect_component_registry
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_ascend_city_blessing as ascend_actions
import test_bound_effect_programs as actions
import test_fixed_tap_payments as tap_actions
import test_self_public_zone_casting as zone_actions


OWNER = 'continuous.characteristics.conditional_chosen_designation'
CONDITIONAL = "As long as you have the city's blessing, creatures you control of the chosen type have vigilance."
PARAGRAPH = "Creatures you control of the chosen type get +1/+1. As long as you have the city's blessing, they also have vigilance."


class ConditionalChosenCharacteristicCompilerTests(unittest.TestCase):
    def test_conditional_chosen_queries_and_shared_paragraph_close_without_changing_legacy(self):
        registry = load_default_capability_registry()
        for text, count in (
            (CONDITIONAL, 1),
            (PARAGRAPH, 2),
            ('Creatures of the chosen color get +1/+1. During your turn, they have flying.', 2),
            ('As long as you have 30 or more life, creatures you control of the chosen type get +1/+1.', 1),
        ):
            with self.subTest(text=text):
                compiled = chosen_characteristics_handler(text)
                self.assertIsNotNone(compiled)
                descriptors = (compiled[1],) if isinstance(compiled[1], dict) else compiled[1]
                self.assertEqual(count, len(descriptors))
                for descriptor in descriptors:
                    default_continuous_effect_component_registry().validate(descriptor)
                conditional = descriptors[-1]
                self.assertIn('source_condition', conditional)
                self.assertEqual('continuous.characteristics.chosen-designation.v1', conditional['body']['handler_id'])
                ir = compile_oracle_card(record(text), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertIn(OWNER, ir.faces[0].nodes[0].capability_dependencies)
        old = chosen_characteristics_handler('Creatures you control of the chosen type get +1/+1.')[1]
        self.assertEqual(1, old['schema_version'])
        self.assertEqual({'handler_id', 'schema_version', 'event', 'designation', 'body'}, set(old))

    def test_ambiguous_subjects_unknown_conditions_and_partial_paragraphs_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (
            CONDITIONAL.replace("you have the city's blessing", 'the top card of your library is blue'),
            CONDITIONAL.replace('creatures you control of the chosen type', 'they'),
            PARAGRAPH.replace('they also have vigilance', 'it also has vigilance'),
            PARAGRAPH.replace('get +1/+1', 'get +X/+X'),
            PARAGRAPH.replace('vigilance', 'vigilance and an unrepresented ability'),
            PARAGRAPH + ' Draw a card.',
            'Creatures you control of the chosen type get +1/+1 for each land you control. During your turn, they have flying.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(chosen_characteristics_handler(text))
                self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=registry,
                                                               capability_profile='commander_review').status)

    def test_conditional_designation_dependencies_and_descriptors_fail_closed(self):
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (OWNER, 'continuous.characteristics.chosen_designation',
                           'continuous.characteristics.fixed_public_state', 'player.designation.citys_blessing'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['conditional chosen dependency withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record(CONDITIONAL), capability_registry=CapabilityRegistry(changed),
                                                           capability_profile='commander_review').status)
        descriptor = chosen_characteristics_handler(CONDITIONAL)[1]
        for change in ({'schema_version': True}, {'unknown': True}, {'body': {'handler_id': 'unrepresented'}}):
            with self.assertRaises(SemanticNodeError):
                default_continuous_effect_component_registry().validate({**descriptor, **change})
        changed = deepcopy(descriptor)
        changed['source_condition']['unknown'] = True
        with self.assertRaises(SemanticNodeError):
            default_continuous_effect_component_registry().validate(changed)
        changed = deepcopy(descriptor)
        changed['body']['body']['condition']['predicate']['subtypes_all'] = ['elf']
        with self.assertRaises(SemanticNodeError):
            default_continuous_effect_component_registry().validate(changed)


class ConditionalChosenCharacteristicRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    lands = ascend_actions.AscendRuntimeTests.lands
    blessed = ascend_actions.AscendRuntimeTests.blessed
    advance_to = tap_actions.FixedTapPaymentRuntimeTests.advance_to

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'conditional-chosen.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/entry-designation-cards.json',
            ROOT / 'tests/fixtures/conditional-query-declaration-cards.json',
            ROOT / 'tests/fixtures/granted-skulk-cards.json',
            ROOT / 'tests/fixtures/ascend-city-blessing-cards.json',
            ROOT / 'tests/fixtures/conditional-chosen-characteristic-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditional chosen witness', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def choose_type(self, session, kind):
        self.resolve(session)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        decision = session.packet('pilot:A', full=True)['decision']
        selected = next(row['id'] for row in decision['ctx']['options'] if row['id'].endswith(':' + kind))
        accepted = session.act('pilot:A', {'action_id': 'choose', 'choices': {'replacement': selected}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)

    def stats(self, session, card):
        current = session.state.cards[card.object_id]
        data = session.engine._effective_card_data(current)
        return data['power'], data['toughness'], 'vigilance' in session.engine._combat_keywords(current)

    def test_actual_radiant_entry_choice_late_tenth_and_vigilance_attack_use_live_query(self):
        session = self.session(413000)
        engine = session.engine
        self.lands(engine, 6)
        source = self.add(engine, 'Radiant Destiny', zone='hand', ref='chosen-original-radiant')
        elf = self.add(engine, 'Llanowar Elves', ref='chosen-current-elf')
        human = self.add(engine, 'Generic Conditional Detective', ref='chosen-unrelated-human')
        elf.acquired_control_turn_count = human.acquired_control_turn_count = -1
        foreign = self.add(engine, 'Llanowar Elves', seat='B', ref='chosen-foreign-elf')
        late = self.add(engine, 'Llanowar Elves', zone='hand', ref='chosen-late-tenth')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='chosen-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'W': 1, 'G': 3})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve(session)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        decision = session.packet('pilot:A', full=True)['decision']
        selected = next(row['id'] for row in decision['ctx']['options'] if row['id'].endswith(':elf'))
        before = authoritative_state_hash(session.state)
        for principal, choice in (('pilot:D', selected), ('pilot:A', selected.removesuffix(':elf') + ':artifact')):
            self.assertFalse(session.act(principal, {'action_id': 'choose', 'choices': {'replacement': choice}}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'choices': {'replacement': selected}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('elf', session.state.cards[source.object_id].annotations['chosen_creature_type'])
        self.assertFalse(self.blessed(session))
        self.assertEqual(('2', '2', False), self.stats(session, elf))
        self.assertEqual(('2', '4', False), self.stats(session, human))
        self.assertEqual(('1', '1', False), self.stats(session, foreign))
        self.cast(session, 'A', late)
        self.resolve(session)
        self.assertTrue(self.blessed(session))
        for card in (elf, late):
            self.assertEqual(('2', '2', True), self.stats(session, card))
        self.assertEqual(('2', '4', False), self.stats(session, human))
        self.advance_to(session, 'combat.attackers')
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {elf.ref: 'B', human.ref: 'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.cards[elf.object_id].tapped)
        self.assertTrue(session.state.cards[human.object_id].tapped)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_real_source_control_departure_and_fresh_entry_choice_recompute_grants(self):
        session = self.session(413010)
        engine = session.engine
        self.lands(engine, 7)
        source = self.add(engine, 'Radiant Destiny', zone='hand', ref='chosen-moving-radiant')
        elf = self.add(engine, 'Llanowar Elves', ref='chosen-old-controller-elf')
        human = self.add(engine, 'Generic Conditional Detective', ref='chosen-second-entry-human')
        foreign = self.add(engine, 'Llanowar Elves', seat='D', ref='chosen-new-controller-elf')
        control = self.add(engine, 'Generic Chosen Source Control', seat='D', zone='hand', ref='chosen-real-control')
        bounce = self.add(engine, 'Generic Skulk Bounce', zone='hand', ref='chosen-real-bounce')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'W': 2})
        session.state.players['D'].mana_pool.update(W=1)
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.choose_type(session, 'elf')
        self.assertTrue(self.blessed(session))
        self.assertEqual(('2', '2', True), self.stats(session, elf))
        self.cast(session, 'D', control, [source.ref])
        self.resolve(session)
        self.assertEqual('D', session.state.cards[source.object_id].controller)
        self.assertFalse(self.blessed(session, 'D'))
        self.assertEqual(('1', '1', False), self.stats(session, elf))
        self.assertEqual(('2', '2', False), self.stats(session, foreign))
        session = self.replay(session, load=True)
        self.cast(session, 'A', bounce, [session.state.cards[source.object_id].ref])
        self.resolve(session)
        self.assertNotIn('chosen_creature_type', session.state.cards[source.object_id].annotations)
        self.assertEqual(('1', '1', False), self.stats(session, foreign))
        self.cast(session, 'A', session.state.cards[source.object_id])
        self.choose_type(session, 'human')
        self.assertEqual('human', session.state.cards[source.object_id].annotations['chosen_creature_type'])
        self.assertEqual(('3', '5', True), self.stats(session, human))
        self.assertEqual(('1', '1', False), self.stats(session, elf))
        self.assertTrue(self.blessed(session))
        self.replay(session, load=True)
