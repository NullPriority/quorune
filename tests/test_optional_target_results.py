from __future__ import annotations

"""CR 107.1b/113.7a/400.7/608.2b,h: optional targets precede dependent queries."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.optional_target_result_model import (
    OptionalTargetResultSpec, OPTIONAL_TARGET_RESULT_CAPABILITY as CAPABILITY,
    OPTIONAL_TARGET_RESULT_FIELD as FIELD, OPTIONAL_TARGET_RESULT_TEMPLATE as TEMPLATE,
)
from quorune.oracle_ir import _reviewed_effect_template, compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.optional_target_results import (
    bind_optional_target_result, optional_target_result_node_capabilities,
    optional_target_result_payload_dependencies,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as controls
import test_bound_effect_programs as bound_controls

FIXTURE = ROOT / 'tests/fixtures/optional-target-results-cards.json'
BODY = "Exile up to one other target creature. That creature's controller gains life equal to its power."


def lower(text=BODY):
    return _reviewed_effect_template(text, card_name='Optional Result Source',
        source_is_permanent=True, source_card_types=('creature',))


class OptionalTargetResultModelTests(unittest.TestCase):
    def test_closed_versioned_dependency_distinguishes_absent_target(self):
        spec = OptionalTargetResultSpec()
        self.assertEqual(spec, OptionalTargetResultSpec.from_dict(spec.to_dict()))
        for targets in ([], (), [None]):
            self.assertTrue(spec.target_is_absent(targets))
        self.assertFalse(spec.target_is_absent(['target']))
        for invalid in ({'index': True}, {'schema_version': True}, {'index': 1},
                        {'schema_version': 2}, {'extra': 1}):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, TypeError)):
                OptionalTargetResultSpec.from_dict({**spec.to_dict(), **invalid})
        for targets in (None, 'target', ['one', 'two'], [False], ['']):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                spec.target_is_absent(targets)

    def test_optional_guard_strips_only_explicit_dependency_and_preserves_legacy(self):
        _, effects, _, _ = lower()
        result = effects[1]
        self.assertIsNone(bind_optional_target_result(result, []))
        self.assertIsNone(bind_optional_target_result(result, [None]))
        mandatory = {k: v for k, v in result.items() if k != FIELD}
        self.assertEqual(mandatory, bind_optional_target_result(result, ['target']))
        self.assertIs(mandatory, bind_optional_target_result(mandatory, []))
        self.assertIn(FIELD, result)

    def test_malformed_payloads_and_target_envelopes_cannot_borrow_authority(self):
        _, effects, schema, mechanics = lower()
        self.assertIn(CAPABILITY, optional_target_result_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for change in ({'op': 'draw'}, {'player': '$controller'}, {'delta': 3}, {'extra': True},
                       {FIELD: {'schema_version': 1, 'index': 1}},
                       {'delta': {**effects[1]['delta'], 'characteristic': 'toughness'}}):
            altered = {**effects[1], **change}
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                optional_target_result_payload_dependencies(altered)
            self.assertFalse(optional_target_result_node_capabilities(
                effects=(effects[0], altered), target_schema=schema, mechanic_ids=mechanics))
        for changed in ({**schema, 'up_to': True}, {**schema, 'up_to': 2},
                        {**schema, 'count': 1}, {**schema, 'source_exclusion': False},
                        {**schema, 'types_any': ['artifact', 'creature']}):
            self.assertFalse(optional_target_result_node_capabilities(
                effects=effects, target_schema=changed, mechanic_ids=mechanics))
        self.assertFalse(optional_target_result_node_capabilities(
            effects=({**effects[0], 'maximum_targets': True}, effects[1]),
            target_schema=schema, mechanic_ids=mechanics))

    def test_runtime_guard_precedes_scalar_controller_and_state_access(self):
        from quorune.semantic_runtime.executor import prepare_stack_resolution_effect
        _, effects, _, _ = lower()
        host = SimpleNamespace(_semantic_value=lambda *_: self.fail('Absent target was queried'))
        item = SimpleNamespace(targets=[])
        self.assertIsNone(prepare_stack_resolution_effect(host, item, effects[1]))
        item.targets = [None]
        self.assertIsNone(prepare_stack_resolution_effect(host, item, effects[1]))
        with patch('quorune.rules.optional_target_results.bind_optional_target_result',
                   side_effect=lambda effect, targets: effect):
            with self.assertRaises((AssertionError, AttributeError)):
                prepare_stack_resolution_effect(host, item, effects[1])


class OptionalTargetResultCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, body=BODY, registry=None):
        card = replace(bound_controls.record('When this creature enters, ' + body,
            'Creature — Elemental'), name='Optional Result Source')
        return compile_oracle_card(card, capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_exact_optional_grammar_preserves_source_span_and_owned_shape(self):
        for body in (BODY, BODY.replace('other ', ''),
                     BODY.replace("That creature's controller", 'Its controller')):
            with self.subTest(body=body):
                ir = self.compile(body)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(TEMPLATE, node.template_id)
                self.assertEqual(1, node.target_schema['up_to'])
                self.assertNotIn('count', node.target_schema)
                self.assertEqual('other ' in body, node.target_schema.get('source_exclusion', False))
                text = 'When this creature enters, ' + body
                self.assertEqual(text[node.span.start:node.span.end], node.text)
                self.assertIn(CAPABILITY, node.capability_dependencies)

    def test_unsupported_counts_roles_recipients_and_material_siblings_stay_residual(self):
        for body in (BODY.replace('one ', 'two '), BODY.replace('creature', 'artifact'),
                     BODY.replace('its power', 'its toughness'),
                     BODY.replace("That creature's controller gains", 'You gain'),
                     BODY + ' Draw cards equal to its toughness and discard one at random.'):
            with self.subTest(body=body):
                self.assertNotEqual('exact', self.compile(body).status)

    def test_all_primitive_authorities_and_optional_compiler_are_required(self):
        for missing in (CAPABILITY, 'permanent.exile.effect', 'life.change.effect',
                        'resolution.target_controller_information', 'quantity_expression.scalar_effect_amount',
                        'resolution.effect.fixed_homogeneous_target_set', 'target.revalidate_resolution'):
            raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id'] == missing).update(
                status='blocked', blockers=['optional target authority withdrawn'])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            with self.subTest(missing=missing):
                self.assertNotEqual('exact', self.compile(registry=registry).status)
        with patch('quorune.compiler.optional_target_result_templates.optional_target_result_template', return_value=None):
            self.assertNotEqual('exact', self.compile().status)

    def test_complete_solitude_closes_without_dropping_any_printed_ability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'optional-originals.sqlite3'
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                semantics = SemanticRegistry()
                for name in ('Solitude', 'Swords to Plowshares', 'Condemn'):
                    card = db.lookup(name, fuzzy=False)
                    register_generated_programs(db, semantics, (card,), trust_level='provisional',
                        capability_registry=self.registry, capability_profile='commander_review',
                        promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                        promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                    program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                        capability_registry=self.registry, capability_profile='commander_review')
                    binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                    self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                    self.assertEqual('capability_closed', binding['trust_basis'])
                    if name == 'Solitude':
                        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                        self.assertEqual('exact', ir.status, ir.material_residuals)
                        self.assertIn(TEMPLATE, [n.template_id for f in ir.faces for n in f.nodes])
                        self.assertIn('casting.evoke.fixed_hand_exile', program.capability_dependencies)
                        ability = next(p for p in semantics.programs_for_oracle(card.oracle_id)
                            if any(FIELD in effect for effect in p.effects))
                        deps = tuple(c for c in ability.capability_dependencies if c != CAPABILITY)
                        changed = replace(ability, coverage=(), capability_dependencies=deps,
                            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                            profile='commander_review')
                        self.assertFalse(bound['strict'])
                        self.assertTrue(any(CAPABILITY in blocker for blocker in bound['blockers']), bound['blockers'])
                        with patch('quorune.compiler.optional_target_result_templates.optional_target_result_template', return_value=None):
                            self.assertNotEqual('exact', compile_oracle_card(card,
                                capability_registry=self.registry, capability_profile='commander_review').status)


class OptionalTargetResultRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.AttachmentFollowupRuntimeTests.session
    add = controls.AttachmentFollowupRuntimeTests.add
    prepare = controls.AttachmentFollowupRuntimeTests.prepare
    closed = controls.AttachmentFollowupRuntimeTests.closed
    actions = controls.AttachmentFollowupRuntimeTests.actions
    cast_offered = controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint = controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = controls.AttachmentFollowupRuntimeTests.resolve
    replay = controls.AttachmentFollowupRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'optional-results.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/nonbasic-land-source-untap-cards.json',
            FIXTURE,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Optional target result', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_white_orchid_phantom_exact_trigger_and_guard(self):
        from quorune.optional_target_result_model import (
            OPTIONAL_LAND_CONTROLLER_SEARCH_CAPABILITY, OPTIONAL_LAND_CONTROLLER_SEARCH_TEMPLATE,
        )
        from quorune.semantic_runtime.executor import prepare_stack_resolution_effect

        record = self.db.lookup("White Orchid Phantom", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry,
                                 capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (node for face in ir.faces for node in face.nodes
                 if node.template_id == OPTIONAL_LAND_CONTROLLER_SEARCH_TEMPLATE)
        self.assertEqual(record.oracle_text[node.span.start:node.span.end], node.text)
        self.assertEqual(1, node.target_schema["up_to"])
        self.assertEqual(["basic"], node.target_schema["supertypes_none"])
        self.assertIn(OPTIONAL_LAND_CONTROLLER_SEARCH_CAPABILITY, node.capability_dependencies)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"]
                          if row["id"] == OPTIONAL_LAND_CONTROLLER_SEARCH_CAPABILITY)
        capability.update(status="blocked", blockers=["The optional land search authority was withheld"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        refused = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", refused.status)
        self.assertIn(OPTIONAL_LAND_CONTROLLER_SEARCH_CAPABILITY, str(refused.material_residuals))

        prefix, optional = node.effects
        search, = optional["effects"]
        for forged in (
            {**optional, "player": "$controller"},
            {**optional, "effects": [{**search, "searching_player": "$controller"}]},
            {**optional, "effects": [{**search, "count": {"minimum": True, "maximum": 1}}]},
            {**optional, "effects": [{**search, "enters_tapped_override": 1}]},
            {key: value for key, value in optional.items() if key != FIELD},
        ):
            with self.subTest(forged=forged):
                self.assertFalse(optional_target_result_node_capabilities(
                    effects=(prefix, forged), target_schema=node.target_schema, mechanic_ids=node.mechanics))
        self.assertFalse(optional_target_result_node_capabilities(
            effects=(prefix, optional), target_schema={**node.target_schema, "supertypes_none": []},
            mechanic_ids=node.mechanics))
        body = record.oracle_text.split("enters, ", 1)[1]
        self.assertIsNone(lower(body.replace("nonbasic land", "creature"))[0])
        host = SimpleNamespace(_semantic_value=lambda *_: self.fail("Absent land was queried"))
        item = SimpleNamespace(targets=[])
        self.assertIsNone(prepare_stack_resolution_effect(host, item, optional))
        with patch("quorune.rules.optional_target_results.bind_optional_target_result",
                   side_effect=lambda effect, targets: effect):
            with self.assertRaises((AssertionError, AttributeError)):
                prepare_stack_resolution_effect(host, item, optional)

    def start_white_orchid(self, seed, *, target_name="Command Tower", response=False):
        session = self.session(seed)
        source = self.add(session.engine, "White Orchid Phantom", zone="hand")
        target = self.add(session.engine, target_name, seat="B", ref="orchid-nonbasic")
        session.engine.change_control(target.object_id, "C", reason="Nonbasic land test control")
        basic = self.add(session.engine, "Generic Bound Plains", seat="C", ref="orchid-legal-basic")
        chosen = self.add(session.engine, "Generic Bound Plains", seat="C", zone="library",
                          ref="orchid-library-basic")
        other = self.add(session.engine, "Generic Bound Body", seat="C", zone="library",
                         ref="orchid-library-ineligible")
        bounce = self.add(session.engine, "Boomerang", zone="hand") if response else None
        self.prepare(session, source, {"W": 2, "U": 2})
        self.checkpoint(session)
        self.cast_offered(session, source)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(target.ref, str(ctx))
        self.assertNotIn(basic.ref, str(ctx))
        self.reject(session, {"action_id": "choose", "targets": [basic.ref]})
        self.reject(session, {"action_id": "choose", "targets": [target.ref, target.ref]})
        self.assertTrue({"flying", "first strike"} <=
                        session.engine._combat_keywords(session.state.cards[source.object_id]))
        return session, target, chosen, other, bounce

    def test_white_orchid_phantom_zero_and_illegal_targets_skip_search(self):
        for offset, stale in enumerate((False, True)):
            with self.subTest(stale=stale):
                session, target, chosen, _, bounce = self.start_white_orchid(493100 + offset, response=stale)
                before = session.state.players["C"].stats.get("shuffle_count", 0)
                self.announce(session, (target.ref,) if stale else ())
                if stale:
                    self.cast_offered(session, bounce, targets=(target.ref,))
                self.assertIsNone(self.resolve(session))
                self.assertEqual("hand" if stale else "battlefield", session.state.cards[target.object_id].zone)
                self.assertEqual("library", session.state.cards[chosen.object_id].zone)
                self.assertEqual(before, session.state.players["C"].stats.get("shuffle_count", 0))
                self.replay(session, load=True)

    def test_white_orchid_phantom_search_uses_each_legal_targets_controller(self):
        cases = (("Command Tower", "graveyard", "apply"),
                 ("Darksteel Citadel", "battlefield", "apply"),
                 ("Darksteel Citadel", "battlefield", "decline"))
        for offset, (name, zone, option) in enumerate(cases):
            with self.subTest(name=name, option=option):
                session, target, chosen, other, _ = self.start_white_orchid(493110 + offset, target_name=name)
                shuffles = {seat: session.state.players[seat].stats.get("shuffle_count", 0) for seat in "ABCD"}
                self.announce(session, (target.ref,))
                self.assertIsNotNone(self.resolve(session))
                self.assertEqual("offer_optional_effect", session.packet("pilot:C", full=True)["decision"]["ctx"]["operation"])
                self.assertEqual(["pilot:C"], list(session.pending_principals()))
                self.assertEqual("B", session.state.cards[target.object_id].owner)
                self.assertEqual(zone, session.state.cards[target.object_id].zone)
                answer = {"action_id": "choose", "choice": option}
                for seat in "ABD":
                    self.reject(session, answer, seat=seat)
                result = session.act("pilot:C", answer)
                self.assertTrue(result.ok, result.summary)
                if option == "apply":
                    self.assertEqual("semantic.search", self.resolve(session).kind)
                    self.assertEqual(["pilot:C"], list(session.pending_principals()))
                    schema = session.packet("pilot:C", full=True)["decision"]["legal_actions"][0]["choice_schema"]
                    self.assertIn(chosen.ref, schema["legal_refs"])
                    self.assertNotIn(other.ref, schema["legal_refs"])
                    self.assertTrue(schema["rules_may_fail_to_find"])
                    for seat in "ABD":
                        projected = str(session.packet("pilot:" + seat, full=True))
                        self.assertNotIn(chosen.ref, projected)
                        self.assertNotIn(other.ref, projected)
                    session = self.replay(session, load=True)
                    self.reject(session, {"action_id": "choose", "search_cards": [other.ref]}, seat="C")
                    result = session.act("pilot:C", {"action_id": "choose", "search_cards": [chosen.ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("battlefield", session.state.cards[chosen.object_id].zone)
                    self.assertEqual("C", session.state.cards[chosen.object_id].controller)
                    self.assertTrue(session.state.cards[chosen.object_id].tapped)
                    shuffles["C"] += 1
                else:
                    self.assertEqual("library", session.state.cards[chosen.object_id].zone)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(shuffles, {seat: session.state.players[seat].stats.get("shuffle_count", 0)
                                            for seat in "ABCD"})
                self.replay(session, load=True)

    def start(self, seed, *, target=True, evoke=False, extra=(), controlled=False, permanents=()):
        session = self.session(seed)
        source = self.add(session.engine, 'Solitude', zone='hand')
        victim = self.add(session.engine, 'Ornithopter', seat='B', ref='optional-target') if target else None
        if victim is not None and controlled:
            session.engine.change_control(victim.object_id, 'A')
        secret = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='optional-private')
        responses = [self.add(session.engine, name, zone='hand', ref='optional-response-' + str(i))
                     for i, name in enumerate(extra)]
        for index, name in enumerate(permanents):
            permanent = self.add(session.engine, name, ref='optional-control-' + str(index))
            self.closed(session, permanent)
        price = self.add(session.engine, 'Swords to Plowshares', zone='hand', ref='optional-evoke-price') if evoke else None
        self.prepare(session, source, {'C': 20, 'W': 10, 'U': 10, 'G': 10, 'R': 10})
        self.checkpoint(session)
        offered = next(a for a in self.actions(session) if a['id'] == 'cast:' + source.ref)
        command = {'action_id': offered['id'], 'pay': 'auto'}
        if evoke:
            self.assertIn(price.ref, next(o for o in offered['cost_options'] if o['id'] == 'evoke')
                ['choice_schema']['exile_cards']['legal_refs'])
            command.update(cost_option='evoke', exile_cards=[price.ref])
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        return session, source, victim, secret, responses, price

    def announce(self, session, targets=(), *, body_first=True):
        for _ in range(24):
            decision = self.resolve(session)
            if decision is None:
                return
            if decision.kind == 'trigger.order':
                principal = session.pending_principals()[0]
                rows = session.packet(principal, full=True)['decision']['ctx']['triggers']
                rows = sorted(rows, key=lambda row: ('evoke' not in row['label'].casefold()) if body_first
                    else ('evoke' in row['label'].casefold()))
                accepted = session.act(principal, {'action_id': 'order', 'triggers': [r['id'] for r in rows]})
                self.assertTrue(accepted.ok, accepted.summary)
                continue
            self.assertEqual('semantic.target', decision.kind)
            accepted = session.act(session.pending_principals()[0], {'action_id': 'choose', 'targets': list(targets)})
            self.assertTrue(accepted.ok, accepted.summary)
            return
        self.fail('Optional trigger announcement did not settle')

    def private_replay(self, session, secret):
        for seat in 'ABC':
            self.assertNotIn(secret.ref, str(session.packet('pilot:' + seat, full=True)))
        return self.replay(session, load=True)

    def reject(self, session, command, seat='A'):
        before = authoritative_state_hash(session.state)
        result = session.act('pilot:' + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_actual_zero_target_is_available_with_only_source_and_replays(self):
        session, source, _, secret, _, _ = self.start(300100, target=False)
        decision = self.resolve(session)
        self.assertEqual('semantic.target', decision.kind)
        ctx = session.packet('pilot:A', full=True)['decision']['ctx']
        self.assertNotIn(source.ref, str(ctx.get('legal_refs', [])))
        self.reject(session, {'action_id': 'choose', 'targets': [source.ref]})
        self.reject(session, {'action_id': 'choose', 'targets': []}, seat='B')
        session = self.private_replay(session, secret)
        self.announce(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual([40] * 4, [session.state.players[p].life for p in 'ABCD'])
        self.private_replay(session, secret)

    def test_actual_evoke_zero_and_selected_target_both_orders_survive_source_sacrifice(self):
        for index, (empty, first) in enumerate(((True, True), (False, True), (False, False))):
            with self.subTest(empty=empty, body_first=first):
                session, source, target, secret, _, price = self.start(300110 + index, evoke=True)
                targets = () if empty else (target.ref,)
                self.announce(session, targets, body_first=first)
                session = self.private_replay(session, secret)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.assertEqual('exile', session.state.cards[price.object_id].zone)
                self.assertEqual('battlefield' if empty else 'exile', session.state.cards[target.object_id].zone)
                self.assertEqual([40] * 4, [session.state.players[p].life for p in 'ABCD'])
                self.private_replay(session, secret)

    def test_actual_target_offer_and_command_reject_source_duplicates_and_unknown(self):
        session, source, target, secret, _, _ = self.start(300120)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        ctx = session.packet('pilot:A', full=True)['decision']['ctx']
        self.assertIn(target.ref, str(ctx))
        for targets in ([source.ref], [target.ref, target.ref], ['missing-target']):
            self.reject(session, {'action_id': 'choose', 'targets': targets})
        self.announce(session, (target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.private_replay(session, secret)

    def test_actual_departure_controller_and_pumped_power_use_resolution_information(self):
        session, source, target, secret, responses, _ = self.start(300130,
            extra=('Blind with Anger', 'Giant Growth'))
        self.announce(session, (target.ref,))
        self.cast_offered(session, responses[0], targets=(target.ref,))
        self.cast_offered(session, responses[1], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.assertEqual('B', session.state.cards[target.object_id].owner)
        self.assertEqual([43, 40, 40, 40], [session.state.players[p].life for p in 'ABCD'])
        self.private_replay(session, secret)

    def test_actual_blinked_target_is_illegal_and_new_incarnation_is_untouched(self):
        session, _, target, secret, responses, _ = self.start(300140, extra=('Ephemerate',), controlled=True)
        old = session.state.cards[target.object_id].logical_object_id
        self.announce(session, (target.ref,))
        self.cast_offered(session, responses[0], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        self.assertNotEqual(old, session.state.cards[target.object_id].logical_object_id)
        self.assertEqual([40] * 4, [session.state.players[p].life for p in 'ABCD'])
        self.private_replay(session, secret)

    def test_actual_source_departure_keeps_body_and_negative_power_gains_zero(self):
        session, source, target, secret, responses, _ = self.start(300150,
            extra=('Unsummon', 'Disorient'))
        self.announce(session, (target.ref,))
        self.cast_offered(session, responses[0], targets=(source.ref,))
        self.cast_offered(session, responses[1], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.assertEqual([40] * 4, [session.state.players[p].life for p in 'ABCD'])
        self.private_replay(session, secret)

    def test_actual_original_life_prohibition_keeps_exile_without_the_gain(self):
        session, _, target, secret, responses, _ = self.start(300160,
            extra=('Giant Growth',), permanents=('Erebos, God of the Dead',))
        self.announce(session, (target.ref,))
        self.cast_offered(session, responses[0], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.assertEqual([40] * 4, [session.state.players[p].life for p in 'ABCD'])
        self.private_replay(session, secret)
