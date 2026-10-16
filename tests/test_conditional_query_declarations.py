from __future__ import annotations

"""CR 109.5/611.3: conditional declaration grants follow the current query."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.declaration_nodes import fixed_static_declaration_grant_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.conditional_continuous import ConditionalDeclarationGrantHandler
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_ascend_city_blessing as ascend_actions
import test_bound_effect_programs as actions
import test_fixed_tap_payments as tap_actions
import test_self_public_zone_casting as zone_actions


OWNER = 'continuous.declaration.conditional_query_grant'
CITY_TEXT = "As long as you have the city's blessing, Detectives you control can't be blocked."


class ConditionalQueryDeclarationCompilerTests(unittest.TestCase):
    def test_pure_query_declaration_uses_one_condition_without_invented_characteristics(self):
        registry = load_default_capability_registry()
        for text, subtype, exclude in (
            (CITY_TEXT, ['detective'], False),
            ('During your turn, other creatures you control can\'t block.', [], True),
            ('As long as you have 30 or more life, creatures you control can\'t be blocked.', [], False),
        ):
            with self.subTest(text=text):
                compiled = fixed_static_declaration_grant_handler(text, source_name='Query witness')
                self.assertIsNotNone(compiled)
                descriptor = compiled[1]
                ConditionalDeclarationGrantHandler().validate(descriptor)
                self.assertEqual('continuous.declaration.public-state.v1', descriptor['handler_id'])
                self.assertEqual(6, descriptor['schema_version'])
                self.assertEqual('fixed_query', descriptor['target']['kind'])
                self.assertEqual('source_controller', descriptor['target']['target_controller'])
                self.assertEqual(subtype, descriptor['target']['predicate']['subtypes_all'])
                self.assertEqual(exclude, descriptor['target']['exclude_source'])
                modifier = descriptor['modifier']
                self.assertEqual([], modifier['add_abilities'])
                self.assertEqual((0, 0), (modifier['power'], modifier['toughness']))
                self.assertEqual(1, len(modifier['add_ability_fragments']))
                ir = compile_oracle_card(record(text), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertIn(OWNER, ir.faces[0].nodes[0].capability_dependencies)

    def test_open_condition_query_siblings_and_source_relative_rules_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (
            CITY_TEXT.replace("you have the city's blessing", 'the top card of your library is blue'),
            CITY_TEXT.replace('Detectives you control', 'those creatures'),
            CITY_TEXT.replace("can't be blocked", "can't be blocked and rewrite history"),
            CITY_TEXT.replace("can't be blocked", "can't be blocked by creatures controlled by your opponents"),
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_static_declaration_grant_handler(text, source_name='Query witness'))
                ir = compile_oracle_card(record(text), capability_registry=registry, capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)

    def test_query_declaration_dependencies_and_descriptor_validation_fail_closed(self):
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (OWNER, 'combat.declaration.typed_components',
                           'continuous.characteristics.fixed_public_state', 'player.designation.citys_blessing'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['conditional query dependency withdrawn'])
            ir = compile_oracle_card(record(CITY_TEXT), capability_registry=CapabilityRegistry(changed),
                                    capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
        descriptor = fixed_static_declaration_grant_handler(CITY_TEXT, source_name='Query witness')[1]
        for field, value in (('add_ability_fragments', []), ('power', True)):
            changed = deepcopy(descriptor)
            changed['modifier'][field] = value
            with self.assertRaises(SemanticNodeError):
                ConditionalDeclarationGrantHandler().validate(changed)
        changed = deepcopy(descriptor)
        changed['target']['predicate']['controller'] = 'A'
        with self.assertRaises(SemanticNodeError):
            ConditionalDeclarationGrantHandler().validate(changed)


class ConditionalQueryDeclarationRuntimeTests(unittest.TestCase):
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
        path = Path(cls.temp.name) / 'conditional-query-declarations.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json',
            ROOT / 'tests/fixtures/granted-skulk-cards.json',
            ROOT / 'tests/fixtures/ascend-city-blessing-cards.json',
            ROOT / 'tests/fixtures/conditional-query-declaration-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditional declaration witness', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_detective_cast_late_recipient_and_block_offer_commit_agree(self):
        for city in (False, True):
            with self.subTest(city=city):
                session = self.session(412000 + city)
                engine = session.engine
                self.lands(engine, 7 if city else 2)
                source = self.add(engine, 'Detective of the Month', zone='hand', ref='query-original-detective')
                detective = self.add(engine, 'Generic Conditional Detective', ref='query-detective-attacker')
                human = self.add(engine, 'Generic Bound Body', ref='query-human-attacker')
                detective.acquired_control_turn_count = human.acquired_control_turn_count = -1
                late = self.add(engine, 'Generic Conditional Detective', zone='hand', ref='query-late-detective')
                first = self.add(engine, 'Generic Bound Body', seat='B', ref='query-first-blocker')
                second = self.add(engine, 'Generic Bound Body', seat='B', ref='query-second-blocker')
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='query-private')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'U': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.resolve(session)
                self.assertEqual(city, self.blessed(session))
                self.cast(session, 'A', late)
                self.resolve(session)
                self.assertEqual(not city, engine._can_block(late, first)[0])
                self.assertTrue(engine._can_block(human, first)[0])
                self.advance_to(session, 'combat.attackers')
                accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {detective.ref: 'B', human.ref: 'B'}})
                self.assertTrue(accepted.ok, accepted.summary)
                self.advance_to(session, 'combat.blockers')
                domains = session.state.pending_decision.payload_by_actor['B']['declaration_constraints']['domains']
                self.assertEqual(not city, detective.ref in domains[first.ref])
                self.assertIn(human.ref, domains[first.ref])
                session = self.replay(session, load=True)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:C', {'action_id': 'block', 'blocks': {first.ref: human.ref}}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if city:
                    self.assertFalse(session.act('pilot:B', {'action_id': 'block', 'blocks': {first.ref: detective.ref}}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                blocks = {second.ref: human.ref}
                if not city:
                    blocks[first.ref] = detective.ref
                accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': blocks})
                self.assertTrue(accepted.ok, accepted.summary)
                for _ in range(32):
                    session.engine.pump()
                    if session.state.phase != 'combat':
                        break
                    self.assertEqual('priority', session.state.pending_decision.kind)
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                else:
                    self.fail('Combat did not finish')
                self.assertEqual(38 if city else 40, session.state.players['B'].life)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_real_source_control_and_departure_recompute_condition_and_recipients(self):
        for new_city in (False, True):
            with self.subTest(new_city=new_city):
                session = self.session(412010 + new_city)
                engine = session.engine
                self.lands(engine, 9)
                self.lands(engine, 9 if new_city else 1, seat='D')
                source = self.add(engine, 'Detective of the Month', ref='query-current-controller-source')
                old = self.add(engine, 'Generic Conditional Detective', ref='query-old-controller-detective')
                current = self.add(engine, 'Generic Conditional Detective', seat='D', ref='query-new-controller-detective')
                blocker = self.add(engine, 'Generic Bound Body', seat='B', ref='query-current-controller-blocker')
                control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='query-real-source-control')
                bounce = self.add(engine, 'Generic Skulk Bounce', zone='hand', ref='query-real-source-departure')
                self.assert_closed(session, source)
                self.prepare(session, actor='D', active='D', mana={'W': 1})
                self.checkpoint(session)
                self.assertTrue(self.blessed(session))
                self.assertFalse(self.blessed(session, 'D'))
                self.assertFalse(engine._can_block(old, blocker)[0])
                self.assertTrue(engine._can_block(current, blocker)[0])
                self.cast(session, 'D', control, [source.ref])
                self.resolve(session)
                session = self.replay(session, load=True)
                engine = session.engine
                source, old, current, blocker = (session.state.cards[c.object_id] for c in (source, old, current, blocker))
                self.assertEqual('D', source.controller)
                self.assertTrue(self.blessed(session))
                self.assertEqual(new_city, self.blessed(session, 'D'))
                self.assertTrue(engine._can_block(old, blocker)[0])
                self.assertEqual(not new_city, engine._can_block(current, blocker)[0])
                self.cast(session, 'A', bounce, [source.ref])
                self.resolve(session)
                self.assertEqual('hand', source.zone)
                self.assertTrue(engine._can_block(current, blocker)[0])
                self.assertTrue(self.blessed(session))
                self.assertEqual(new_city, self.blessed(session, 'D'))
                self.replay(session, load=True)
