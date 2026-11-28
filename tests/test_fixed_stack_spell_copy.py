from __future__ import annotations

"""Pinned CR 707.10/115.7d: one stack spell copy with optional new targets."""

import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.validation import program_source_is_current
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.stack_spell_copy_model import FixedStackSpellCopySpec, STACK_SPELL_COPY_CAPABILITY
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-stack-spell-copy-cards.json'
UNSUPPORTED_COPY_CONTROL = 'Fixed Stack Copy Boundary Witness'


class FixedStackSpellCopyModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, text='Copy target instant or sorcery spell. You may choose new targets for the copy.'):
        return CardRecord(oracle_id='fixture:stack-copy-boundary', name='Stack Copy Witness',
            mana_cost='{R}{R}', mana_value=2, type_line='Instant', oracle_text=text,
            power=None, toughness=None, loyalty=None, defense=None, colors=('R',), color_identity=('R',),
            keywords=(), produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={})

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_fixed_stack_copy_spec_is_closed_and_current_controller_scoped(self):
        for controlled in (False, True):
            spec = FixedStackSpellCopySpec(controlled)
            self.assertEqual(spec, FixedStackSpellCopySpec.from_dict(spec.to_dict()))
            self.assertEqual(['stack'], spec.target_schema['zones'])
            self.assertEqual(['instant', 'sorcery'], spec.target_schema['types_any'])
            self.assertEqual('you' if controlled else None, spec.target_schema.get('controller_relation'))
        for value in ({'schema_version': True, 'controlled_only': False},
                {'schema_version': 1, 'controlled_only': 1},
                {'schema_version': 1, 'controlled_only': False, 'count': 2}):
            with self.assertRaises(ValueError):
                FixedStackSpellCopySpec.from_dict(value)

    def test_fixed_stack_copy_source_span_and_exact_shape_reject_unknown_variants(self):
        from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
        for controlled in (False, True):
            text='Copy target instant or sorcery spell' + (' you control' if controlled else '') + '. You may choose new targets for the copy.'
            ir = self.compile(self.record(text))
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))
            self.assertIn(STACK_SPELL_COPY_CAPABILITY, node.capability_dependencies)
            self.assertEqual(FixedStackSpellCopySpec(controlled).effects, node.effects)
            self.assertTrue(fixed_stack_spell_copy_node_capabilities(effects=node.effects,
                target_schema=node.target_schema, mechanic_ids=node.mechanics))
            self.assertFalse(fixed_stack_spell_copy_node_capabilities(effects=({**node.effects[0], 'count': 2},),
                target_schema=node.target_schema, mechanic_ids=node.mechanics))
        for text in ('Copy target instant or sorcery spell twice. You may choose new targets for the copies.',
                'Copy target creature spell. You may choose new targets for the copy.',
                'Copy target instant or sorcery spell, except that the copy is red. You may choose new targets for the copy.',
                'Copy target instant or sorcery spell.'):
            self.assertNotEqual('exact', self.compile(self.record(text)).status)
        self.assertNotEqual('exact', self.compile(self.record(self.record().oracle_text + '\nInvent an unrepresented sibling.')).status)
        registry = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (STACK_SPELL_COPY_CAPABILITY, 'target.revalidate_resolution'):
            changed = deepcopy(registry)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['Copy owner withdrawn'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_copy_then_owner_hand_binds_only_its_ordered_shared_stack_target(self):
        from quorune.rules.opponent_spell_or_permanent_return import COPY_RETURN_FIELD
        from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities

        clause = "Copy target instant or sorcery spell, then return it to its owner's hand."
        text = clause + " You may choose new targets for the copy."
        ir = self.compile(self.record(text))
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        copy, move = node.effects
        self.assertEqual(FixedStackSpellCopySpec().effects, (copy,))
        self.assertEqual(FixedStackSpellCopySpec().target_schema, node.target_schema)
        self.assertEqual({"op": "move", "card": "$target.0", "destination": "hand", COPY_RETURN_FIELD: 1}, move)
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        for effects in ((move, copy), (copy, {**move, "card": "$target.1"}),
                        (copy, {**move, "destination": "exile"}), (copy, {**move, COPY_RETURN_FIELD: True})):
            self.assertFalse(fixed_stack_spell_copy_node_capabilities(
                effects=effects, target_schema=node.target_schema, mechanic_ids=node.mechanics,
            ))
        for variant in (text.replace("then return it", "then return another spell"),
                        text.replace("its owner's hand", "your hand"), clause):
            self.assertNotEqual("exact", self.compile(self.record(variant)).status)

    def test_fixed_stack_copy_producer_omission_fails_independent_positive_contract(self):
        with mock.patch('quorune.compiler.fixed_stack_spell_copy.fixed_stack_spell_copy_template', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_fixed_stack_copy_source_span_and_exact_shape_reject_unknown_variants()


class FixedStackSpellCopyRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'stack-copy.sqlite3'
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/colored-hand-reveal-cards.json",
            ROOT / "tests/fixtures/opponent-spell-or-permanent-return-cards.json",
            ROOT / "tests/fixtures/targeted-spell-control-cards.json",
            ROOT / "tests/fixtures/optional-draw-attack-restriction-cards.json",
            FIXTURE,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Stack copy witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = 'trusted_only'
        return actions.SpreeModeCostRuntimeTests.prepare(self, session, **kwargs)

    def resolve_top(self, session):
        ref = session.state.stack[-1].ref
        for _ in range(16):
            if not any(item.ref == ref for item in session.state.stack):
                return
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Top stack item did not resolve through ordinary priority')

    def copy_activation(self, session, seat, source):
        for _ in range(16):
            if session.state.priority_player == seat:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        return next(row for row in session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if row.get('source') == source.ref and row['id'].startswith('activate:')
            and 'stack' in json.dumps(row.get('target_schema', {})))

    def test_targeted_discard_resolving_copy_uses_only_the_closed_shape(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.targeted_discard_spell_copy import CAPABILITY, COPY_OFFER_FIELD, copy_offer

        card = self.db.lookup("Chain of Smog")
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        self.assertEqual(copy_offer(), node.effects[1])
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for text in (
            card.oracle_text.replace("Target player", "Target opponent"),
            card.oracle_text.replace("two cards", "three cards"),
            card.oracle_text.replace("That player may", "You may"),
            card.oracle_text.replace("That player may", "If they discarded two cards this way, that player may"),
        ):
            changed = compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
            )
            self.assertFalse(any(CAPABILITY in effect.capability_dependencies
                                 for face in changed.faces for effect in face.nodes), text)
        session = self.session(70710301)
        self.add(session.engine, card.name, zone="hand")
        program, = (row for row in session.engine.semantics.programs_for_oracle(card.oracle_id)
                    if CAPABILITY in row.capability_dependencies)
        bad_effects = (
            (program.effects[0], copy_offer("$controller")),
            (program.effects[0], {**copy_offer(), COPY_OFFER_FIELD: True}),
            ({**program.effects[0], "count": 1}, program.effects[1]),
            tuple(reversed(program.effects)),
        )
        for effects in bad_effects:
            bound = bind_semantic_program_runtime(
                replace(program, effects=effects), capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(bound["strict"])

    def test_targeted_discard_resolving_copy_grammar_withdrawal_is_detected(self):
        from quorune.rules.targeted_discard_spell_copy import CAPABILITY

        card = self.db.lookup("Chain of Smog")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        before = compile_oracle_card(card, **options)
        self.assertEqual("exact", before.status, before.material_residuals)
        self.assertIn(CAPABILITY, before.faces[0].nodes[0].capability_dependencies)
        with mock.patch("quorune.compiler.targeted_discard_spell_copy.targeted_discard_spell_copy_template",
                        return_value=None):
            omitted = compile_oracle_card(card, **options)
        self.assertFalse(omitted.faces[0].nodes[0].exact)
        self.assertEqual(card.oracle_text, omitted.material_residuals[0].text)

    def test_targeted_discard_resolving_copy_controls_each_copy_and_replays(self):
        from quorune.record import authoritative_state_hash

        session = self.session(70710302)

        def choose(actor, **response):
            accepted = session.act("pilot:" + actor, {"action_id": "choose", **response})
            self.assertTrue(accepted.ok, accepted.summary)

        engine = session.engine
        source = self.add(engine, "Chain of Smog", zone="hand", ref="resolving-copy-original")
        for seat in ("B", "C"):
            for object_id in tuple(session.state.players[seat].zones["hand"]):
                engine.move_card(object_id, "library", log=False)
        hand = tuple(self.add(engine, "Generic Bound Plains", seat="B", zone="hand", ref=f"private-discard-{i}")
                     for i in range(3))
        self.assert_closed(session, source)
        self.prepare(session, mana={"B": 1, "C": 1})
        self.checkpoint(session)
        self.cast(session, "A", source, ["B"])
        original = session.state.stack[-1]
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        self.assertEqual(["pilot:B"], session.pending_principals())
        for seat in ("A", "C", "D"):
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            for card in hand:
                self.assertNotIn(card.ref, json.dumps(packet))
        before = authoritative_state_hash(session.state)
        invalid = session.act("pilot:A", {"action_id": "choose", "cards": [hand[0].ref, hand[1].ref]})
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act("pilot:B", {"action_id": "choose", "cards": [hand[0].ref, hand[1].ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual(["graveyard", "graveyard", "hand"],
                         [session.state.cards[card.object_id].zone for card in hand])
        session = self.replay(session, load=True)
        choose("B", choice="copy")
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        choose("B")
        first, = session.state.stack
        self.assertEqual(("spell_copy", "B", ["B"], original.semantic_key),
                         (first.kind, first.controller, first.targets, first.semantic_key))
        self.assertNotEqual(original.ref, first.ref)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        self.assertEqual(["pilot:B"], session.pending_principals())
        choose("B", cards=[hand[2].ref])
        self.assertEqual("graveyard", session.state.cards[hand[2].object_id].zone)
        choose("B", choice="copy")
        before = authoritative_state_hash(session.state)
        invalid = session.act("pilot:B", {"action_id": "choose", "targets": ["C", "D"]})
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        choose("B", targets=["C"])
        second, = session.state.stack
        self.assertEqual(("spell_copy", "B", ["C"], original.semantic_key),
                         (second.kind, second.controller, second.targets, second.semantic_key))
        self.assertNotEqual(first.ref, second.ref)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(["pilot:C"], session.pending_principals())
        self.assertFalse(session.state.players["C"].zones["hand"])
        choose("C", choice="decline")
        self.assertFalse(session.state.stack)
        self.assertEqual(1, len(session.engine._current_turn_history("spell_cast")))
        self.replay(session)

    def test_actual_narset_copies_then_returns_foreign_owned_spell_and_keeps_or_changes_targets(self):
        for retarget in (False, True):
            with self.subTest(retarget=retarget):
                session = self.session(70710201 + retarget)
                reversal = self.add(session.engine, "Narset's Reversal", zone="hand")
                takeover = self.add(session.engine, "Aethersnatch", zone="hand")
                growth = self.add(session.engine, "Giant Growth", seat="B", zone="hand")
                first = self.add(session.engine, "Llanowar Elves", ref="narset-original-target")
                second = self.add(session.engine, "Llanowar Elves", ref="narset-replacement-target")
                for card in (reversal, takeover, growth):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"C": 4, "U": 6})
                session.state.players["B"].mana_pool["G"] = 1
                self.checkpoint(session)

                self.cast(session, "B", growth, [first.ref])
                original_ref = session.state.stack[-1].ref
                self.cast(session, "A", takeover, [original_ref])
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                result = session.act("pilot:A", {"action_id": "choose"})
                self.assertTrue(result.ok, result.summary)
                original = next(item for item in session.state.stack if item.ref == original_ref)
                self.assertEqual("A", original.controller)
                self.assertEqual("B", session.state.cards[growth.object_id].owner)

                self.cast(session, "A", reversal, [original_ref])
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertEqual("stack", session.state.cards[growth.object_id].zone)
                self.assertEqual(original_ref, session.state.stack[0].ref)
                self.reject(session, "choose", "B", targets=[second.ref])
                self.reject(session, "choose", targets=[first.ref, second.ref])
                session = self.replay(session, load=True)
                choice = {"action_id": "choose", **({"targets": [second.ref]} if retarget else {})}
                result = session.act("pilot:A", choice)
                self.assertTrue(result.ok, result.summary)

                copy, = session.state.stack
                expected = second if retarget else first
                self.assertEqual(("spell_copy", "A", [expected.ref]), (copy.kind, copy.controller, copy.targets))
                self.assertNotEqual(original_ref, copy.ref)
                self.assertEqual("hand", session.state.cards[growth.object_id].zone)
                self.assertIn(growth.object_id, session.state.players["B"].zones["hand"])
                self.assertNotIn(growth.object_id, session.state.players["A"].zones["hand"])
                self.assertEqual(3, len(session.engine._current_turn_history("spell_cast")))
                self.assertEqual([1, 1], [session.engine._numeric_stat(card.object_id, "power")
                                          for card in (first, second)])
                self.assertIsNone(self.resolve(session))
                self.assertEqual([1, 4] if retarget else [4, 1], [
                    session.engine._numeric_stat(card.object_id, "power") for card in (first, second)
                ])
                self.assertEqual(3, len(session.engine._current_turn_history("spell_cast")))
                self.replay(session, load=True)

    def test_actual_narset_loses_its_single_target_without_copy_or_return(self):
        session = self.session(70710210)
        reversal = self.add(session.engine, "Narset's Reversal", zone="hand")
        growth = self.add(session.engine, "Giant Growth", seat="B", zone="hand")
        counter = self.add(session.engine, "Counterspell", seat="C", zone="hand")
        target = self.add(session.engine, "Llanowar Elves")
        self.assert_closed(session, reversal)
        self.prepare(session, mana={"U": 2})
        session.state.players["B"].mana_pool["G"] = 1
        session.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(session)
        self.cast(session, "B", growth, [target.ref])
        original_ref = session.state.stack[-1].ref
        self.cast(session, "A", reversal, [original_ref])
        self.cast(session, "C", counter, [original_ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[growth.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[reversal.object_id].zone)
        self.assertEqual(1, session.engine._numeric_stat(target.object_id, "power"))
        self.assertFalse(any(card.is_spell_copy for card in session.state.cards.values()))
        self.replay(session, load=True)

    def test_actual_narset_copies_and_returns_targetless_uncounterable_spell(self):
        session = self.session(70710211)
        reversal = self.add(session.engine, "Narset's Reversal", zone="hand")
        fallout = self.add(session.engine, "Volcanic Fallout", seat="B", zone="hand")
        self.assert_closed(session, reversal)
        self.assert_closed(session, fallout)
        self.prepare(session, mana={"U": 2})
        session.state.players["B"].mana_pool.update({"C": 1, "R": 2})
        self.checkpoint(session)
        self.cast(session, "B", fallout)
        original = session.state.stack[-1]
        self.assertFalse(session.engine._stack_item_can_be_countered(original))
        self.cast(session, "A", reversal, [original.ref])
        self.resolve_top(session)
        copied, = session.state.stack
        self.assertEqual(("spell_copy", "A", []), (copied.kind, copied.controller, copied.targets))
        self.assertIn(fallout.object_id, session.state.players["B"].zones["hand"])
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in "ABCD"])
        self.assertFalse(any(event.code == "stack.counter" and event.details.get("stack") == original.ref
                             for event in session.state.events))
        self.assertEqual(2, len(session.engine._current_turn_history("spell_cast")))
        self.assertIsNone(self.resolve(session))
        self.assertEqual([38] * 4, [session.state.players[seat].life for seat in "ABCD"])
        self.replay(session, load=True)

    def test_actual_reverberate_copies_a_spell_with_independent_targets_and_full_replay(self):
        session = self.session(70710001)
        engine = session.engine
        source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-reverberate')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-original')
        first = self.add(engine, 'Llanowar Elves', ref='stack-copy-first')
        second = self.add(engine, 'Llanowar Elves', ref='stack-copy-second')
        enemy = self.add(engine, 'Llanowar Elves', seat='B', ref='stack-copy-untouched')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='stack-copy-private')
        self.assert_closed(session, source)
        self.assert_closed(session, growth)
        self.prepare(session, mana={'G': 1, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [first.ref])
        original = session.state.stack[-1]
        self.cast(session, 'A', source, [original.ref])
        pending = self.resolve(session)
        self.assertEqual('semantic.choice', pending.kind)
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        self.reject(session, 'choose', 'B', targets=[second.ref])
        self.reject(session, 'choose', targets=[first.ref, second.ref])
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [second.ref]})
        self.assertTrue(result.ok, result.summary)
        copies = [item for item in session.state.stack if item.kind == 'spell_copy']
        self.assertEqual(1, len(copies))
        self.assertEqual((original.semantic_key, [second.ref], []),
            (copies[0].semantic_key, copies[0].targets, copies[0].modes))
        self.assertEqual([first.ref], next(item for item in session.state.stack if item.ref == original.ref).targets)
        self.resolve(session)
        self.assertEqual(4, session.engine._numeric_stat(first.object_id, 'power'))
        self.assertEqual(4, session.engine._numeric_stat(second.object_id, 'power'))
        self.assertEqual(1, session.engine._numeric_stat(enemy.object_id, 'power'))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_original_countered_before_copy_producer_resolves_creates_no_copy(self):
        session = self.session(70710002)
        engine = session.engine
        source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-stale-producer')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-stale-original')
        target = self.add(engine, 'Llanowar Elves', ref='stack-copy-stale-body')
        counter = self.add(engine, 'Counterspell', seat='B', zone='hand', ref='stack-copy-stale-counter')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'G': 1, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [target.ref])
        original_ref = session.state.stack[-1].ref
        self.cast(session, 'A', source, [original_ref])
        self.cast(session, 'B', counter, [original_ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.engine._numeric_stat(target.object_id, 'power'))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[growth.object_id].zone)
        self.assertFalse(any(card.is_spell_copy for card in session.state.cards.values()))
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_retain_old_target_incarnation_or_choose_returned_creature_as_new_target(self):
        for change in (False, True):
            with self.subTest(change=change):
                session = self.session(70710003 + change)
                engine = session.engine
                source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-incarnation-producer')
                growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-incarnation-original')
                target = self.add(engine, 'Ashcoat Bear', ref='stack-copy-incarnation-target')
                bounce = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='stack-copy-incarnation-bounce')
                self.assert_closed(session, source)
                engine.state.players['B'].mana_pool['U'] = 1
                self.prepare(session, mana={'C': 1, 'G': 2, 'R': 2})
                self.checkpoint(session)
                self.cast(session, 'A', growth, [target.ref])
                original = session.state.stack[-1]
                snapshot = deepcopy(original.context['target_snapshots'][target.ref])
                old_identity = target.logical_object_id
                self.cast(session, 'A', source, [original.ref])
                self.cast(session, 'B', bounce, [target.ref])
                self.resolve_top(session)
                self.assertEqual('hand', session.state.cards[target.object_id].zone)
                self.cast(session, 'A', target)
                self.resolve_top(session)
                self.assertNotEqual(old_identity, session.state.cards[target.object_id].logical_object_id)
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                session = self.replay(session, load=True)
                response = {'action_id': 'choose', **({'targets': [{'ref': target.ref}]} if change else {})}
                result = session.act('pilot:A', response)
                self.assertTrue(result.ok, result.summary)
                copied = next(item for item in session.state.stack if item.kind == 'spell_copy')
                current_snapshot = copied.context['target_snapshots'][target.ref]
                self.assertEqual(not change, current_snapshot == snapshot)
                self.resolve(session)
                self.assertEqual(5 if change else 2, session.engine._numeric_stat(target.object_id, 'power'))
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_native_naru_entry_copy_target_is_restricted_to_current_controller(self):
        session = self.session(70710005)
        engine = session.engine
        mage = self.add(engine, 'Naru Meha, Master Wizard', zone='hand', ref='stack-copy-naru')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-naru-own-spell')
        shock = self.add(engine, 'Shock', seat='B', zone='hand', ref='stack-copy-naru-other-spell')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-naru-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, mage)
        engine.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'C': 2, 'U': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        own_ref = session.state.stack[-1].ref
        self.cast(session, 'B', shock, ['D'])
        other_ref = session.state.stack[-1].ref
        self.cast(session, 'A', mage)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        schema = json.dumps(session.packet('pilot:A', full=True)['decision']['ctx']['target_schema'])
        self.assertIn(own_ref, schema)
        self.assertNotIn(other_ref, schema)
        self.reject(session, 'choose', targets=[other_ref])
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [own_ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual(38, session.state.players['D'].life)
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_native_dualcaster_copies_opponents_spell_under_trigger_controllers_authority(self):
        session = self.session(70710006)
        engine = session.engine
        mage = self.add(engine, 'Dualcaster Mage', zone='hand', ref='stack-copy-dualcaster')
        shock = self.add(engine, 'Shock', seat='B', zone='hand', ref='stack-copy-dualcaster-other-spell')
        self.assert_closed(session, mage)
        engine.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'C': 1, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'B', shock, ['D'])
        original_ref = session.state.stack[-1].ref
        self.cast(session, 'A', mage)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.reject(session, 'choose', 'B', targets=[original_ref])
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [original_ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': ['B']})
        self.assertTrue(result.ok, result.summary)
        copied = next(item for item in session.state.stack if item.kind == 'spell_copy')
        self.assertEqual(('A', ['B']), (copied.controller, copied.targets))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual((38, 38), (session.state.players['B'].life, session.state.players['D'].life))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_unsupported_source_siblings_have_no_independent_executable_copy_fallback(self):
        session = self.session(70710007)
        engine = session.engine
        record = self.db.lookup(UNSUPPORTED_COPY_CONTROL)
        isolated = replace(record, oracle_text=record.oracle_text.splitlines()[0])
        standalone = compile_oracle_card(isolated, capability_registry=self.registry,
                                        capability_profile='commander_review')
        complete = compile_oracle_card(record, capability_registry=self.registry,
                                      capability_profile='commander_review')
        self.assertEqual('exact', standalone.status)
        self.assertIn(STACK_SPELL_COPY_CAPABILITY, standalone.faces[0].nodes[0].capability_dependencies)
        self.assertNotEqual('exact', complete.status)
        self.assertTrue(complete.material_residuals)
        source = self.add(engine, UNSUPPORTED_COPY_CONTROL, zone='hand', ref='stack-copy-incomplete-source')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-incomplete-target')
        body = self.add(engine, 'Llanowar Elves', ref='stack-copy-incomplete-body')
        self.prepare(session, mana={'C': 2, 'R': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [body.ref])
        target_ref = session.state.stack[-1].ref
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn('cast:' + source.ref, [row['id'] for row in actions])
        self.reject(session, 'cast:' + source.ref, targets=[target_ref], pay='auto')
        self.resolve(session)
        self.assertEqual(4, session.engine._numeric_stat(body.object_id, 'power'))
        self.replay(session)

    def test_copy_source_buyback_pays_extra_and_returns_only_its_own_physical_card(self):
        session = self.session(70710010)
        engine = session.engine
        source = self.add(engine, 'Reiterate', zone='hand', ref='stack-copy-buyback')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-buyback-original')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-buyback-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'R': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'cost_option': 'buyback',
            'targets': [original_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[growth.object_id].zone)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        copies = [card for card in session.state.cards.values() if card.is_spell_copy]
        self.assertEqual(1, len(copies))
        self.assertTrue(all(card.zone == 'outside' for card in copies))
        self.replay(session)

    def test_flare_copy_alternative_sacrifices_current_red_creature_instead_of_mana(self):
        session = self.session(70710011)
        engine = session.engine
        source = self.add(engine, 'Flare of Duplication', zone='hand', ref='stack-copy-flare')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-flare-original')
        creature = self.add(engine, 'Raging Goblin', ref='stack-copy-flare-red-creature')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-flare-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        option = next(row for row in offered['cost_options'] if sum(row['requirements'].values()) == 0)
        self.reject(session, offered['id'], cost_option=option['id'], targets=[original_ref],
            sacrifice_cards=[bodies[0].ref], pay='auto')
        result = session.act('pilot:A', {'action_id': offered['id'], 'cost_option': option['id'],
            'targets': [original_ref], 'sacrifice_cards': [creature.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[creature.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_public_alternative_price_declares_reused_nonmana_payment_owner(self):
        from dataclasses import replace
        from quorune.public_alternative_costs import FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime
        program=compile_best_available_card_program(self.db,self.db.lookup('Flare of Duplication'),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        price=next(ability for ability in program.abilities
            if FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY in ability.capability_dependencies)
        payment='casting.additional_cost.fixed_sacrifice'
        self.assertIn(payment,price.capability_dependencies)
        remaining=tuple(value for value in price.capability_dependencies if value!=payment)
        stripped=replace(price,capability_dependencies=remaining,
            capability_closure=self.registry.closure(remaining,profile='commander_review').to_dict())
        binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+payment,binding['blockers'])

    def test_permanent_spell_and_activated_ability_are_not_eligible_spell_copy_targets(self):
        for ability in (False, True):
            with self.subTest(ability=ability):
                session = self.session(70710012 + ability)
                engine = session.engine
                source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-wrong-kind')
                other = self.add(engine, 'Prodigal Pyromancer' if ability else 'Ashcoat Bear', seat='B',
                    zone='battlefield' if ability else 'hand', ref='stack-copy-wrong-kind-source')
                other.acquired_control_turn_count = -1
                self.assert_closed(session, source)
                self.prepare(session, mana={'R': 2})
                engine.state.players['B'].mana_pool.update({'C': 1, 'G': 1})
                self.checkpoint(session)
                if ability:
                    while session.state.priority_player != 'B':
                        self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                    offered = next(row for row in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                        if row.get('source') == other.ref and row['id'].startswith('activate:'))
                    result = session.act('pilot:B', {'action_id': offered['id'], 'targets': ['D'], 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.cast(session, 'B', other)
                target_ref = session.state.stack[-1].ref
                while session.state.priority_player != 'A':
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                self.assertNotIn('cast:' + source.ref, [row['id'] for row in actions])
                self.reject(session, 'cast:' + source.ref, targets=[target_ref], pay='auto')
                self.resolve(session)
                self.assertEqual('hand', session.state.cards[source.object_id].zone)
                self.assertEqual(2, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_original_copy_candidates_need_complete_siblings_and_current_generated_registration(self):
        expected = {'Geistblast', 'Reiterate', 'Dual Casting', 'Sigil Tracer', 'Naru Meha, Master Wizard',
            'Mirrorpool', 'Dualcaster Mage', 'Twincast', 'Uyo, Silent Prophet', 'Reverberate',
            'Nivix Guildmage', 'Flare of Duplication', 'Kitsa, Otterball Elite', 'Stella Lee, Wild Card',
            'Fury Storm', 'Insidious Will', 'Lithoform Engine'}
        pattern = re.compile(r'Copy target instant or sorcery spell(?: you control)?\. You may choose new targets for the copy\.', re.I)
        rows = [r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']
                if r['name'] != UNSUPPORTED_COPY_CONTROL and pattern.search(r.get('oracle_text') or '')]
        self.assertEqual(24, len(rows))
        for row in rows:
            with self.subTest(name=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(row['name'] in expected, binding['strict_capability_ready'], binding)
                if row['name'] in expected:
                    session = self.session(70710008)
                    source = self.add(session.engine, row['name'], zone='hand', ref='stack-copy-original-table')
                    self.assert_closed(session, source)
                    generated = [p for p in session.engine.semantics.programs_for_oracle(record.oracle_id)
                        if 'fixed-stack-spell-copy' in p.coverage]
                    self.assertTrue(generated)
                    self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in generated))

    def test_native_geistblast_exiles_itself_from_graveyard_and_retains_copy_choice(self):
        session = self.session(70710014)
        engine = session.engine
        source = self.add(engine, 'Geistblast', zone='graveyard', ref='stack-copy-geistblast')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-geistblast-original')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-geistblast-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'U': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'A', source)
        self.reject(session, action['id'], 'B', targets=[original_ref], pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [original_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_native_sigil_tracer_taps_two_current_wizards_including_itself(self):
        session = self.session(70710015)
        engine = session.engine
        source = self.add(engine, 'Sigil Tracer', ref='stack-copy-sigil')
        wizard = self.add(engine, 'Dualcaster Mage', ref='stack-copy-sigil-wizard')
        wrong = self.add(engine, 'Llanowar Elves', ref='stack-copy-sigil-nonwizard')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-sigil-original')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'U': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [wrong.ref])
        original_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'A', source)
        for refs in ([source.ref], [source.ref, source.ref], [source.ref, wrong.ref]):
            self.reject(session, action['id'], targets=[original_ref], cost_cards=refs, pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [original_ref],
            'cost_cards': [source.ref, wizard.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in (source, wizard)))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(7, session.engine._numeric_stat(wrong.object_id, 'power'))
        self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_native_dual_casting_uses_recipient_controller_instead_of_aura_controller(self):
        session = self.session(70710016)
        engine = session.engine
        aura = self.add(engine, 'Dual Casting', zone='hand', ref='stack-copy-dual-casting')
        recipient = self.add(engine, 'Llanowar Elves', seat='B', ref='stack-copy-granted-recipient')
        recipient.acquired_control_turn_count = -1
        own_body = self.add(engine, 'Llanowar Elves', ref='stack-copy-aura-controller-body')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-aura-controller-spell')
        shock = self.add(engine, 'Shock', seat='B', zone='hand', ref='stack-copy-recipient-controller-spell')
        self.assert_closed(session, aura)
        engine.state.players['B'].mana_pool['R'] = 2
        self.prepare(session, mana={'C': 1, 'R': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', aura, [recipient.ref])
        self.resolve(session)
        self.cast(session, 'A', growth, [own_body.ref])
        foreign_ref = session.state.stack[-1].ref
        self.cast(session, 'B', shock, ['C'])
        own_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'B', recipient)
        self.reject(session, action['id'], 'A', targets=[own_ref], pay='auto')
        self.reject(session, action['id'], 'B', targets=[foreign_ref], pay='auto')
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [own_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[recipient.object_id].tapped)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.reject(session, 'choose', 'A', targets=['D'])
        session = self.replay(session, load=True)
        result = session.act('pilot:B', {'action_id': 'choose', 'targets': ['D']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('B', next(item for item in session.state.stack if item.kind == 'spell_copy').controller)
        self.resolve(session)
        self.assertEqual((38, 38), (session.state.players['C'].life, session.state.players['D'].life))
        self.assertEqual(4, session.engine._numeric_stat(own_body.object_id, 'power'))
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_native_uyo_can_use_land_mana_before_returning_two_lands_to_owners_hands(self):
        session = self.session(70710017)
        engine = session.engine
        source = self.add(engine, 'Uyo, Silent Prophet', ref='stack-copy-uyo')
        lands = [self.add(engine, 'Generic Bound Plains', ref='stack-copy-uyo-land-' + str(i)) for i in range(2)]
        lands[1].owner = 'B'
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-uyo-original')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-uyo-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'A', source)
        self.reject(session, action['id'], targets=[original_ref], cost_cards=[lands[0].ref] * 2, pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [original_ref],
            'cost_cards': [card.ref for card in lands], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        for card, owner in zip(lands, 'AB', strict=True):
            current = session.state.cards[card.object_id]
            self.assertEqual((owner, 'hand'), (current.owner, current.zone))
            self.assertIn(current.object_id, session.state.players[owner].zones['hand'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.replay(session)

    def test_whole_source_certificate_is_enforced_without_forcing_trust(self):
        from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_program_is_closed
        session = self.session(70710009)
        source = self.add(session.engine, 'Reverberate', zone='hand', ref='stack-copy-certificate')
        program = next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if 'fixed-stack-spell-copy' in p.coverage)
        self.assertTrue(program_source_is_current(self.db, program))
        self.assertTrue(fixed_stack_spell_copy_program_is_closed(program))
        self.assertIs(program.provenance['requires_complete_card_program'], True)
        for certificate in ({'schema_version': 1, 'oracle_ir_status': 'partial', 'material_residual_count': 1},
                {'schema_version': True, 'oracle_ir_status': 'exact', 'material_residual_count': 0}, None):
            changed = replace(program, provenance={**deepcopy(program.provenance), 'card_program_admission': certificate})
            self.assertFalse(program_source_is_current(self.db, changed))
            self.assertFalse(fixed_stack_spell_copy_program_is_closed(changed))
        missing = replace(program, provenance={key: value for key, value in program.provenance.items()
            if key != 'requires_complete_card_program'})
        self.assertFalse(fixed_stack_spell_copy_program_is_closed(missing))

    def test_stack_copy_commit_omission_preserves_admission_but_fails_native_copy_consequence(self):
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        result = unittest.TestResult()
        witness = type(self)('test_actual_reverberate_copies_a_spell_with_independent_targets_and_full_replay')
        with mock.patch.object(SemanticChoiceIntentHostMixin, 'copy_stack_item_intent', return_value='omitted-copy'):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn('self.assertEqual(1, len(copies))', result.failures[0][1])

    def test_return_copy_preserves_borrowed_controller_payment_and_independent_targets(self):
        from quorune.record import authoritative_state_hash

        for retain in (False, True):
            with self.subTest(retain=retain):
                session = self.session(70710320 + int(retain))
                source = self.add(session.engine, "Chain of Vapor", zone="hand")
                original_target = self.add(session.engine, "Generic Bound Body", seat="C", ref="borrowed-target")
                second_target = self.add(session.engine, "Generic Bound Body", seat="D", ref="followup-target")
                borrowed_land = self.add(session.engine, "Generic Bound Plains", seat="D", ref="borrowed-land")
                blocked_land = self.add(session.engine, "Generic Bound Plains", seat="C", ref="another-land")
                second_land = self.add(session.engine, "Generic Bound Plains", seat="D", ref="followup-land")
                session.engine.change_control(original_target.object_id, "B", reason="Seed borrowed permanent")
                session.engine.change_control(borrowed_land.object_id, "B", reason="Seed borrowed land")
                self.assert_closed(session, source)
                self.prepare(session, mana={"U": 1})
                self.checkpoint(session)
                self.cast(session, "A", source, [original_target.ref])
                original = session.state.stack[-1]
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertEqual(["pilot:B"], session.pending_principals())
                for seat in "ACD":
                    self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
                self.assertEqual("hand", session.state.cards[original_target.object_id].zone)
                self.assertIn(original_target.object_id, session.state.players["C"].zones["hand"])

                before = authoritative_state_hash(session.state)
                for principal, land in (("pilot:C", blocked_land), ("pilot:B", blocked_land)):
                    response = session.act(principal, {"action_id": "choose", "cards": [land.ref]})
                    self.assertFalse(response.ok, response.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                paid = session.act("pilot:B", {"action_id": "choose", "cards": [borrowed_land.ref]})
                self.assertTrue(paid.ok, paid.summary)
                self.assertIn(borrowed_land.object_id, session.state.players["D"].zones["graveyard"])
                self.assertEqual(["pilot:B"], session.pending_principals())
                copy = session.act("pilot:B", {"action_id": "choose", "choice": "copy"})
                self.assertTrue(copy.ok, copy.summary)
                self.assertEqual(["pilot:B"], session.pending_principals())
                payload = session.packet("pilot:B", full=True)["decision"]["ctx"]
                self.assertEqual([original_target.ref], list(payload["default_targets"]))
                before = authoritative_state_hash(session.state)
                invalid = session.act("pilot:B", {"action_id": "choose", "targets": [second_land.ref]})
                self.assertFalse(invalid.ok, invalid.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                answer = {"action_id": "choose"}
                if not retain:
                    answer["targets"] = [second_target.ref]
                accepted = session.act("pilot:B", answer)
                self.assertTrue(accepted.ok, accepted.summary)
                copied, = session.state.stack
                self.assertEqual(("spell_copy", "B", [original_target.ref if retain else second_target.ref]),
                                 (copied.kind, copied.controller, copied.targets))
                self.assertNotEqual(original.ref, copied.ref)
                self.assertEqual([original_target.ref], original.targets)
                if retain:
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("battlefield", session.state.cards[second_target.object_id].zone)
                else:
                    self.assertEqual("semantic.choice", self.resolve(session).kind)
                    self.assertEqual(["pilot:D"], session.pending_principals())
                    self.assertEqual("hand", session.state.cards[second_target.object_id].zone)
                    declined = session.act("pilot:D", {"action_id": "choose", "cards": []})
                    self.assertTrue(declined.ok, declined.summary)
                self.assertFalse(session.state.stack)
                self.assertEqual(1, len(session.engine._current_turn_history("spell_cast")))
                self.replay(session, load=True)

    def test_arbiter_cannot_submit_reserved_return_copy_stages(self):
        from quorune.record import authoritative_state_hash
        from quorune.rules.targeted_discard_spell_copy import COPY_OFFER_FIELD
        from quorune.rules.targeted_return_spell_copy import (
            LAND_PAYMENT_FIELD, RETURN_COPY_VERSION, RETURN_TARGET_FIELD, return_copy_instruction,
        )

        session = self.session(70710325)
        source = self.add(session.engine, "Generic Raw Resolution Witness", zone="hand")
        permanent = self.add(session.engine, "Generic Bound Body", seat="B")
        land = self.add(session.engine, "Generic Bound Plains", seat="B")
        session.state.config.semantic_policy = "arbitrate_or_pause"
        actions.SpreeModeCostRuntimeTests.prepare(self, session, mana={"U": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.assertEqual("arbiter.resolve", self.resolve(session).kind)
        original = session.state.stack[-1]
        payment = {"op": "choose_objects", "player": "B", LAND_PAYMENT_FIELD: 1}
        offer = {"op": "choose_objects", "player": "B", COPY_OFFER_FIELD: RETURN_COPY_VERSION,
                 RETURN_TARGET_FIELD: permanent.ref, "_paid_land": [land.ref, "fabricated"]}
        copied = return_copy_instruction("B", original.ref, permanent.ref)
        nested = {"op": "offer_optional_effect", "player": "A", "effects": [payment]}
        before = authoritative_state_hash(session.state)
        for action in ("resolve", "register_and_resolve"):
            for item in (payment, offer, copied, nested):
                with self.subTest(action=action, effect=item["op"]):
                    result = session.act("arbiter", {"action_id": action, "effects": [item]})
                    self.assertFalse(result.ok, result.summary)
                    self.assertIn("requires a bound card program", result.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)

    def test_return_copy_is_optional_even_after_a_committed_land_sacrifice(self):
        session = self.session(70710324)
        source = self.add(session.engine, "Chain of Vapor", zone="hand")
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        land = self.add(session.engine, "Generic Bound Plains", seat="B")
        self.assert_closed(session, source)
        self.prepare(session, mana={"U": 1})
        self.checkpoint(session)
        self.cast(session, "A", source, [target.ref])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        for response in ({"cards": [land.ref]}, {"choice": "decline"}):
            result = session.act("pilot:B", {"action_id": "choose", **response})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[land.object_id].zone)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)


class ReturnControllerCopyCompilerTests(unittest.TestCase):
    record = FixedStackSpellCopyModelCompilerTests.record

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=load_default_capability_registry(),
                                   capability_profile="commander_review")

    def test_only_whole_return_then_land_sacrifice_and_optional_copy_compiles(self):
        from quorune.rules.targeted_return_spell_copy import return_copy_payment

        text = ("Return target nonland permanent to its owner's hand. Then that permanent's controller "
                "may sacrifice a land of their choice. If the player does, they may copy this spell "
                "and may choose a new target for that copy.")

        def witness():
            ir = self.compile(self.record(text))
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(return_copy_payment(), node.effects[1])
            self.assertEqual(["land"], node.target_schema["types_none"])
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))

        witness()
        with mock.patch("quorune.compiler.targeted_discard_spell_copy.targeted_return_spell_copy_template",
                        return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for altered in (
            text.replace("controller", "owner"),
            text.replace("sacrifice a land", "sacrifice a creature"),
            text.replace("they may copy", "they copy"),
            text.replace("target nonland permanent", "target land"),
            text + " Draw a card.",
        ):
            self.assertNotEqual("exact", self.compile(self.record(altered)).status)
