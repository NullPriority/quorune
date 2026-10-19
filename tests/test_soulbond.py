"""Independent pinned CR 702.95 pairing and complete source contracts."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_scalar_effect_amounts as combat_actions
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class SoulbondCompilerTests(unittest.TestCase):
    def test_two_keyword_abilities_keep_distinct_runtime_identities_and_instances(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler.generated_program_identity import generated_ability_id
        caps = load_default_capability_registry()
        for text, count in (('Soulbond',2), ('Soulbond, Soulbond',4), ('Flying, soulbond',2)):
            record = replace(query_record(text), keywords=('Soulbond','Flying'))
            ir = compile_oracle_card(record, capability_registry=caps)
            self.assertEqual('exact', ir.status)
            nodes = [node for face in ir.faces for node in face.nodes if node.kind == 'triggered_ability']
            self.assertEqual(count, len(nodes))
            ids = [generated_ability_id(kind=node.kind, face_id='front', line=node.span.line,
                static_declaration=False, node_id=node.node_id) for node in nodes]
            self.assertEqual(count, len(set(ids)))
            self.assertEqual({'permanent.enter.self','permanent.enter'}, {node.event for node in nodes})

    def test_keyword_paired_modifier_and_independent_child_dependencies_fail_closed(self):
        from copy import deepcopy
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        text = 'Soulbond\nAs long as this creature is paired with another creature, both creatures have flying.'
        record = replace(query_record(text), keywords=('Soulbond',))
        caps = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(record, capability_registry=caps).status)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cid in ('pairing.soulbond','continuous.soulbond.paired_grant'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == cid).update(status='blocked', blockers=['omitted Soulbond owner'])
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(changed)).status)
        for changed in (text.replace('have flying','have an unrepresented ability'),
                        text.replace('have flying','have "Whenever this creature attacks, do something unknown."'),
                        text.replace('Soulbond\n','Soulbond with an unknown modifier\n')):
            self.assertNotEqual('exact', compile_oracle_card(replace(record,oracle_text=changed), capability_registry=caps).status)


class SoulbondPairModelTests(unittest.TestCase):
    def test_pair_lifetime_predicate_omission_is_detected(self):
        from quorune.soulbond_model import SoulbondMember
        method = 'test_departure_control_type_phase_and_new_incarnation_expire_both_endpoints'
        with patch.object(SoulbondMember, 'matches', return_value=True), self.assertRaises(AssertionError):
            getattr(type(self)(method), method)()

    def objects(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.source_continuity import SourceContinuitySnapshot
        from quorune.soulbond_model import SoulbondObjectSnapshot
        return tuple(SoulbondObjectSnapshot(ContinuousObjectIdentity('object:' + ref, ref + '@0'),
            ref, controller, True, True, history=SourceContinuitySnapshot(0, 0, 0))
            for ref, controller in (('source', 'A'), ('old-partner', 'A'), ('entrant', 'A'), ('enemy', 'B')))

    def pair(self, objects=None):
        from quorune.soulbond_model import propose_pair
        return propose_pair(pair_id='pair:event:1', source_ref='source', partner_ref='old-partner',
            controller='A', objects=self.objects() if objects is None else objects)

    def test_pair_codec_and_symmetric_membership_are_closed(self):
        from quorune.soulbond_model import SoulbondPair, SoulbondError
        objects = self.objects()
        pair = self.pair(objects)
        self.assertEqual(pair, SoulbondPair.from_dict(json.loads(json.dumps(pair.to_dict()))))
        self.assertEqual(objects[1].identity, pair.partner_of(objects[0].identity))
        self.assertEqual(objects[0].identity, pair.partner_of(objects[1].identity))
        self.assertIsNone(pair.partner_of(objects[2].identity))
        for changes in ({'schema_version': True}, {'schema_version': 2}, {'unknown': 1}, {'controller': []}):
            with self.assertRaises(ValueError):
                SoulbondPair.from_dict({**pair.to_dict(), **changes})
        with self.assertRaises(SoulbondError):
            replace(pair, second=pair.first)

    def test_self_entry_and_other_entry_have_distinct_untargeted_candidates(self):
        from quorune.soulbond_model import pairing_candidates
        objects = self.objects()
        self.assertEqual(('entrant', 'old-partner'), pairing_candidates(source_ref='source',
            controller='A', objects=objects))
        self.assertEqual(('entrant',), pairing_candidates(source_ref='source', controller='A',
            objects=objects, entered_ref='entrant'))
        self.assertEqual((), pairing_candidates(source_ref='source', controller='A',
            objects=objects, entered_ref='source'))
        self.assertEqual((), pairing_candidates(source_ref='source', controller='A',
            objects=objects, entered_ref='enemy'))
        self.assertEqual((), pairing_candidates(source_ref='source', controller='B', objects=objects))

    def test_live_pair_cannot_be_replaced_and_other_unpaired_objects_remain_available(self):
        from quorune.soulbond_model import pairing_candidates, propose_pair, SoulbondError
        objects = self.objects()
        pair = self.pair(objects)
        self.assertEqual((), pairing_candidates(source_ref='source', controller='A', objects=objects, pairs=(pair,)))
        self.assertEqual((), pairing_candidates(source_ref='entrant', controller='A', objects=objects, pairs=(pair,)))
        with self.assertRaises(SoulbondError):
            propose_pair(pair_id='pair:event:2', source_ref='source', partner_ref='entrant',
                controller='A', objects=objects, pairs=(pair,))

    def test_control_and_phase_epochs_prevent_restoration_but_untapping_does_not_unpair(self):
        from quorune.soulbond_model import current_pairs
        objects = self.objects()
        pair = self.pair(objects)
        for index in (0, 1):
            for field in ('control_epoch', 'phase_epoch'):
                changed = list(objects)
                changed[index] = replace(objects[index], history=replace(objects[index].history, **{field:1}))
                self.assertEqual((), current_pairs((pair,), changed))
            changed = list(objects)
            changed[index] = replace(objects[index], history=replace(objects[index].history, untap_epoch=1))
            self.assertEqual((pair,), current_pairs((pair,), changed))

    def test_departure_control_type_phase_and_new_incarnation_expire_both_endpoints(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.soulbond_model import current_pairs, pairing_candidates
        objects = self.objects()
        pair = self.pair(objects)
        for index in (0, 1):
            for change in ({'controller':'B'}, {'on_battlefield':False}, {'is_creature':False},
                           {'phased_out':True}, {'history':None},
                           {'identity':ContinuousObjectIdentity(objects[index].identity.object_id, objects[index].ref + '@1')}):
                changed = list(objects)
                changed[index] = replace(objects[index], **change)
                expired = current_pairs((pair,), changed)
                self.assertEqual((), expired)
                self.assertEqual((), current_pairs(expired, objects), 'Expiration must be committed before later restoration')
        invalid_partner = replace(objects[1], is_creature=False)
        self.assertEqual(('entrant',), pairing_candidates(source_ref='source', controller='A',
            objects=(objects[0], invalid_partner, *objects[2:])))

    def test_duplicate_books_and_stale_or_malformed_proposals_reject_without_mutation(self):
        from quorune.soulbond_model import canonical_pair_book, propose_pair, SoulbondError
        objects = self.objects()
        pair = self.pair(objects)
        before = pair.to_dict()
        with self.assertRaises(SoulbondError):
            canonical_pair_book((pair, pair))
        with self.assertRaises(SoulbondError):
            canonical_pair_book((pair, replace(pair, pair_id='different-pair-id')))
        for partner in ('source', 'enemy', 'missing'):
            with self.assertRaises(SoulbondError):
                propose_pair(pair_id='pair:bad', source_ref='source', partner_ref=partner,
                    controller='A', objects=objects)
        self.assertEqual(before, pair.to_dict())
        with self.assertRaises(SoulbondError):
            replace(objects[0], on_battlefield=1)

    def test_opportunities_pin_source_and_other_entry_incarnations(self):
        from quorune.soulbond_model import SoulbondOpportunity
        objects = self.objects()
        opportunity = SoulbondOpportunity('A', objects[0].ref, objects[0].identity.logical_object_id,
            'other_entry', objects[2].ref, objects[2].identity.logical_object_id)
        self.assertEqual(('entrant',), opportunity.candidates(objects))
        self.assertEqual(opportunity, SoulbondOpportunity.from_effect(json.loads(json.dumps(opportunity.to_effect()))))
        for changed in (replace(opportunity, source_identity='source@1'),
                        replace(opportunity, entered_identity='entrant@1')):
            self.assertEqual((), changed.candidates(objects))
        with self.assertRaises(ValueError):
            replace(opportunity, mode=[])
        with self.assertRaises(ValueError):
            SoulbondOpportunity.from_effect({**opportunity.to_effect(), 'extra':True})


class SoulbondRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.JobSelectRuntimeTests.session
    add = actions.JobSelectRuntimeTests.add
    prepare = actions.JobSelectRuntimeTests.prepare
    cast = actions.JobSelectRuntimeTests.cast
    checkpoint = actions.JobSelectRuntimeTests.checkpoint
    resolve = actions.JobSelectRuntimeTests.resolve
    replay = actions.JobSelectRuntimeTests.replay
    resolve_spell = actions.JobSelectRuntimeTests.resolve_spell
    until = combat_actions.EventPlayerSubjectActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'soulbond.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/soulbond-cards.json',
            ROOT / 'tests/fixtures/fixed-basic-type-setting-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json',
            ROOT / 'tests/fixtures/conditional-copy-value-cards.json',
            ROOT / 'tests/fixtures/aggregated-zone-event-triggers.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Soulbond', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_ordinary_wingcrafter_has_complete_normal_binding(self):
        card = self.db.lookup('Wingcrafter')
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def test_original_soulbond_cards_close_only_with_independent_paired_children(self):
        boundaries = {'Donna Noble', 'Breathkeeper Seraph', 'Mirage Phalanx'}
        fixture = json.loads((ROOT/'tests/fixtures/soulbond-cards.json').read_text(encoding='utf-8'))
        semantics = SemanticRegistry()
        for row in fixture['cards']:
            card = self.db.lookup(row['name'])
            program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=self.registry, capability_profile='commander_review')
            with self.subTest(card=card.name):
                self.assertEqual(card.name not in boundaries,
                    bind_card_program_runtime(program, capability_registry=self.registry,
                        profile='commander_review')['strict_capability_ready'])

    def test_paired_grant_omission_is_detected_by_real_entry_and_choice(self):
        from quorune.semantic_runtime.soulbond import SoulbondPairedGrantHandler
        method = 'test_own_entry_offers_optional_untargeted_pair_and_grants_both_flying'
        with patch.object(SoulbondPairedGrantHandler, 'lower', return_value=()), self.assertRaises(AssertionError):
            getattr(type(self)(method), method)()

    def test_own_entry_offers_optional_untargeted_pair_and_grants_both_flying(self):
        session = self.session(702_095_001)
        source = self.add(session.engine, 'Wingcrafter', zone='hand', ref='soulbond-source')
        partner = self.add(session.engine, 'Generic Bound Body', ref='soulbond-partner')
        enemy = self.add(session.engine, 'Generic Bound Body', seat='B', ref='enemy-soulbond-candidate')
        self.prepare(session, mana={'U':2, 'C':4})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        decision = self.resolve(session)
        self.assertIsNotNone(decision, 'A normal Soulbond entry must offer a real pairing choice')
        self.assertEqual('semantic.choice', decision.kind)
        packet = json.dumps(session.packet('pilot:A', full=True)['decision'])
        self.assertIn(partner.ref, packet)
        self.assertNotIn(enemy.ref, packet)
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        command = {'action_id':'choose', 'cards':[partner.ref]}
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        for card in (source, partner):
            self.assertIn('flying', session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[enemy.object_id]))
        self.replay(session, load=True)

    def test_other_entry_only_offers_the_entered_creature_and_decline_does_not_pair(self):
        session = self.session(702_095_002)
        source = self.add(session.engine, 'Wingcrafter', ref='old-soulbond-source')
        older = self.add(session.engine, 'Generic Bound Body', ref='old-unpaired-creature')
        entrant = self.add(session.engine, 'Generic Bound Body', zone='hand', ref='entering-creature')
        self.prepare(session, mana={'W':4,'C':12})
        self.checkpoint(session)
        self.cast(session, 'A', entrant)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        packet = session.packet('pilot:A', full=True)['decision']
        self.assertIn(entrant.ref, json.dumps(packet))
        self.assertNotIn(older.ref, json.dumps(packet))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id':'choose','cards':[older.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id':'choose','cards':[]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.soulbond_pairs)
        self.assertNotIn('flying', session.engine._combat_keywords(source))
        self.replay(session, load=True)

    def test_zone_departure_immediately_unpairs_both_and_removes_flying_from_survivor(self):
        session = self.session(702_095_003)
        source = self.add(session.engine, 'Wingcrafter', zone='hand', ref='departing-soulbond')
        partner = self.add(session.engine, 'Generic Bound Body', ref='surviving-partner')
        self.prepare(session, mana={'U':2,'C':4})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNotNone(self.resolve(session))
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[partner.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        # This owner-level movement contrasts the command-level entry witness.
        session.engine.move_card(source.object_id, 'graveyard', reason='pair departure boundary', semantic_events=True)
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[partner.object_id]))

    def paired_fixture(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, 'Wingcrafter', zone='hand', ref='lifetime-source')
        partner = self.add(session.engine, 'Generic Bound Body', ref='lifetime-partner')
        self.prepare(session, mana={'U':4,'W':8,'G':8,'C':20})
        self.cast(session, 'A', source)
        self.assertIsNotNone(self.resolve(session))
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[partner.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        return session, source, partner

    def test_lost_soulbond_does_not_cancel_pending_trigger_or_end_pair_but_grant_is_removed(self):
        session = self.session(702_095_004)
        source = self.add(session.engine, 'Wingcrafter', zone='hand', ref='ability-loss-source')
        partner = self.add(session.engine, 'Generic Bound Body', ref='ability-loss-partner')
        spell = self.add(session.engine, 'Humble', zone='hand', ref='remove-source-abilities')
        self.prepare(session, mana={'U':3,'W':3,'C':8})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve_spell(session, source)
        self.assertEqual(1, len(session.state.stack))
        self.cast(session, 'A', spell, [session.state.cards[source.object_id].ref])
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[partner.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(session.state.soulbond_pairs))
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[partner.object_id]))
        self.replay(session, load=True)

    def test_creature_type_loss_ends_pair_and_restoring_type_does_not_repair(self):
        from quorune.continuous_effect_model import (ContinuousEffect, ContinuousEffectOrigin,
            ContinuousEffectDuration, ContinuousObjectIdentity, ContinuousOperation, Layer)
        from quorune.continuous_effect_state import commit_continuous_effect, expire_end_of_turn_continuous_effects
        session, source, partner = self.paired_fixture(702_095_005)
        # Registered layer-four journal mutation; this does not claim support
        # for an otherwise unsupported printed type-changing spell.
        commit_continuous_effect(session.state, ContinuousEffect(
            effect_id='pair-type-boundary', source_id='pair-type-boundary-source',
            layer=Layer.TYPE, sublayer='4', timestamp=session.engine._next_zone_timestamp(),
            operations=(ContinuousOperation('set_types', ['Artifact']),),
            origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(ContinuousObjectIdentity(source.object_id, source.logical_object_id),)))
        session.engine._stabilize()
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertNotIn('creature', session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[0])
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[partner.object_id]))
        self.assertEqual(1, expire_end_of_turn_continuous_effects(session.state))
        session.engine._stabilize()
        self.assertIn('creature', session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[0])
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[partner.object_id]))
        self.checkpoint(session)
        self.replay(session, load=True)

    def test_two_soulbond_sources_contribute_independent_stats_and_keywords(self):
        session = self.session(702_095_006)
        first = self.add(session.engine, 'Wingcrafter', ref='paired-wingcrafter')
        second = self.add(session.engine, 'Wolfir Silverheart', zone='hand', ref='paired-wolfir')
        self.prepare(session, mana={'G':4,'C':12})
        self.checkpoint(session)
        self.cast(session, 'A', second)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('trigger.order', decision.kind)
        refs = [item.ref for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(refs))
        self.assertTrue(session.act('pilot:A', {'action_id':'order','triggers':refs}).ok)
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        source_ref = session.packet('pilot:A', full=True)['decision']['ctx']['source']
        other = second if source_ref == first.ref else first
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[other.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(session.state.soulbond_pairs))
        self.assertEqual((5, 5), tuple(session.engine._numeric_stat(first.object_id, field) for field in ('power','toughness')))
        self.assertEqual((8, 8), tuple(session.engine._numeric_stat(second.object_id, field) for field in ('power','toughness')))
        for card in (first, second):
            self.assertIn('flying', session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.replay(session, load=True)

    def test_public_pair_projection_exposes_references_without_private_hands_or_identities(self):
        session, source, partner = self.paired_fixture(702_095_007)
        private = self.add(session.engine, 'Generic Bound Body', seat='C', zone='hand', ref='pair-private-hand')
        def objects(value):
            if isinstance(value, dict):
                if value.get('id') in {source.ref, partner.ref} and 'n' in value:
                    yield value
                for item in value.values():
                    yield from objects(item)
            elif isinstance(value, list):
                for item in value:
                    yield from objects(item)
        for viewer in 'ABCD':
            packet = session.packet('pilot:' + viewer, full=True)
            found = {row['id']:row for row in objects(packet['state'])}
            self.assertEqual(partner.ref, found[source.ref]['paired_with'])
            self.assertEqual(source.ref, found[partner.ref]['paired_with'])
            encoded = json.dumps(packet)
            self.assertNotIn(source.object_id, encoded)
            self.assertNotIn(partner.object_id, encoded)
            if viewer != 'C':
                self.assertNotIn(private.ref, encoded)

    def test_paired_activated_blink_uses_recipient_and_new_incarnation_requires_new_pair(self):
        session = self.session(702_095_008)
        source = self.add(session.engine, 'Deadeye Navigator', zone='hand', ref='paired-navigator')
        partner = self.add(session.engine, 'Generic Bound Body', ref='blink-pair-recipient')
        self.prepare(session, mana={'U':8,'C':20})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[partner.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        prior = session.state.cards[partner.object_id].logical_object_id
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activation = next(row for row in actions if row['id'].startswith('activate:' + partner.ref + ':'))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id':activation['id'],'pay':'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id':activation['id'],'pay':'auto'}).ok)
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertNotEqual(prior, session.state.cards[partner.object_id].logical_object_id)
        self.assertEqual('battlefield', session.state.cards[partner.object_id].zone)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[]}).ok)
        self.assertIsNone(self.resolve(session))
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:' + partner.ref + ':') for row in actions))
        self.replay(session, load=True)

    def test_paired_damage_trigger_survives_control_change_and_keeps_private_draw_controller(self):
        session = self.session(702_095_009)
        source = self.add(session.engine, 'Tandem Lookout', zone='hand', ref='paired-lookout')
        partner = self.add(session.engine, 'Generic Bound Body', ref='lookout-attacker')
        partner.acquired_control_turn_count = -1
        control = self.add(session.engine, 'Generic Event Subject Control', seat='C', zone='hand', ref='steal-paired-attacker')
        self.add(session.engine, 'Generic Bound Plains', seat='C', ref='control-response-land')
        self.prepare(session, mana={'U':4,'C':12})
        self.cast(session, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[partner.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        self.checkpoint(session)
        hands = {seat:set(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        before_life = session.state.players['B'].life
        self.until(session, lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
        self.assertTrue(session.act('pilot:A', {'action_id':'attack','attackers':{partner.ref:'B'}}).ok)
        self.until(session, lambda s:bool(s.state.stack))
        self.assertEqual('A', session.state.stack[-1].controller)
        self.assertEqual(partner.object_id, session.state.stack[-1].source_object_id)
        self.assertEqual(before_life - 2, session.state.players['B'].life)
        session = self.replay(session, load=True)
        self.cast(session, 'C', control, [partner.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('C', session.state.cards[partner.object_id].controller)
        self.assertEqual((), session.state.soulbond_pairs)
        drawn = set(session.state.players['A'].zones['hand']) - hands['A']
        self.assertEqual(1, len(drawn))
        drawn_ref = session.state.cards[next(iter(drawn))].ref
        for seat in 'ABCD':
            self.assertEqual(seat == 'A', drawn_ref in json.dumps(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def test_paired_death_grant_uses_recipient_last_known_power_after_pair_ends(self):
        session = self.session(702_095_016)
        source = self.add(session.engine, 'Doom Weaver', zone='hand', ref='paired-doom-weaver')
        partner = self.add(session.engine, 'Generic Bound Body', ref='doomed-recipient')
        spell = self.add(session.engine, 'Murder', zone='hand', ref='destroy-paired-recipient')
        self.prepare(session, mana={'B':8,'C':20})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[partner.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        before = len(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', spell, [partner.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[partner.object_id].zone)
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertEqual(before + 1, len(session.state.players['A'].zones['hand']), 'Murder costs one card and the recipient last-known power draws two')
        self.replay(session, load=True)

    def test_simultaneous_creature_entries_queue_opportunities_then_only_one_pair_forms(self):
        session = self.session(702_095_017)
        source = self.add(session.engine, 'Wingcrafter', ref='simultaneous-soulbond-source')
        spell = self.add(session.engine, 'Raise the Alarm', zone='hand', ref='two-entering-creatures')
        self.prepare(session, mana={'W':4,'C':8})
        self.checkpoint(session)
        self.cast(session, 'A', spell)
        decision = self.resolve(session)
        self.assertEqual('trigger.order', decision.kind)
        refs = [item.ref for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(refs))
        self.assertTrue(session.act('pilot:A', {'action_id':'order','triggers':refs}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        packet = session.packet('pilot:A', full=True)['decision']
        legal = packet['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual(1, len(legal), 'An other-entry trigger can pair only its own entering token')
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[legal[0]]}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(session.state.soulbond_pairs))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(2, len(tokens))
        self.assertEqual(1, sum('flying' in session.engine._combat_keywords(card) for card in tokens))
        self.replay(session, load=True)

    def test_token_copy_does_not_copy_pair_relation_and_can_form_its_own_pair(self):
        session, source, partner = self.paired_fixture(702_095_018)
        free = self.add(session.engine, 'Generic Bound Body', ref='new-token-partner')
        spell = self.add(session.engine, 'Cackling Counterpart', zone='hand', ref='copy-soulbond-creature')
        self.prepare(session, mana={'U':6,'C':12})
        self.checkpoint(session)
        self.cast(session, 'A', spell, [source.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        token = tokens[0]
        self.assertNotIn('flying', session.engine._combat_keywords(token))
        packet = session.packet('pilot:A', full=True)['decision']
        legal = packet['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual([free.ref], legal)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','cards':[free.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, len(session.state.soulbond_pairs))
        for card in (source, partner, token, free):
            self.assertIn('flying', session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.replay(session, load=True)

    def test_controller_change_before_resolution_invalidates_the_original_pairing_controller(self):
        session = self.session(702_095_019)
        source = self.add(session.engine, 'Wingcrafter', zone='hand', ref='pending-controller-source')
        self.add(session.engine, 'Generic Bound Body', ref='old-controller-candidate')
        spell = self.add(session.engine, 'Generic Event Subject Control', seat='B', zone='hand', ref='change-before-pair')
        self.prepare(session, mana={'U':3,'C':8})
        session.state.players['B'].mana_pool.update({'W':2})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve_spell(session, source)
        self.assertEqual('A', session.state.stack[-1].controller)
        self.cast(session, 'B', spell, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.assertFalse(session.state.soulbond_pairs)
        self.replay(session, load=True)

    def test_phase_owner_ends_pair_and_phase_in_does_not_restore_it(self):
        from quorune.control_effects import record_source_transition
        session, source, partner = self.paired_fixture(702_095_020)
        # Phasing lifecycle owner witness; no printed phasing-card support claim.
        source.phased_out = True
        record_source_transition(session.engine, source, previous_phased_out=False)
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[partner.object_id]))
        source.phased_out = False
        record_source_transition(session.engine, source, previous_phased_out=True)
        session.engine._stabilize()
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[partner.object_id]))


class SoulbondStateOwnerTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    setUpClass = classmethod(SoulbondRuntimeTests.setUpClass.__func__)
    tearDownClass = classmethod(SoulbondRuntimeTests.tearDownClass.__func__)
    session = SoulbondRuntimeTests.session
    add = SoulbondRuntimeTests.add

    def fixture(self, seed):
        session = self.session(seed)
        first = self.add(session.engine, 'Generic Bound Body', ref='pair-owner-first')
        second = self.add(session.engine, 'Generic Bound Body', ref='pair-owner-second')
        third = self.add(session.engine, 'Generic Bound Body', ref='pair-owner-third')
        enemy = self.add(session.engine, 'Generic Bound Body', seat='B', ref='pair-owner-enemy')
        return session, first, second, third, enemy

    def test_pair_commit_and_record_roundtrip_preserve_unused_historical_shape(self):
        from quorune.model import GameState
        from quorune.soulbond_state import commit_soulbond_pair
        session, first, second, third, enemy = self.fixture(702_095_010)
        before = session.state.to_dict()
        self.assertNotIn('soulbond_pairs', before)
        self.assertNotIn('soulbond_pairs', GameState.from_dict(before).to_dict())
        pair = commit_soulbond_pair(session.engine, pair_id='owner:1', source_ref=first.ref,
            partner_ref=second.ref, controller='A')
        self.assertEqual((pair,), session.state.soulbond_pairs)
        self.assertIsNotNone(first.source_continuity)
        self.assertIsNotNone(second.source_continuity)
        data = session.state.to_dict()
        self.assertEqual(data, GameState.from_dict(json.loads(json.dumps(data))).to_dict())
        for value in (None, {}, [pair.to_dict(), pair.to_dict()]):
            with self.assertRaises(ValueError):
                GameState.from_dict({**data, 'soulbond_pairs':value})

    def test_invalid_pair_and_conflicting_proposal_leave_all_authority_unchanged(self):
        from quorune.soulbond_state import commit_soulbond_pair
        from quorune.soulbond_model import SoulbondError
        session, first, second, third, enemy = self.fixture(702_095_011)
        for ref in (first.ref, enemy.ref, 'missing-object'):
            before = authoritative_state_hash(session.state)
            with self.assertRaises(SoulbondError):
                commit_soulbond_pair(session.engine, pair_id='invalid-pair', source_ref=first.ref,
                    partner_ref=ref, controller='A')
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertIsNone(first.source_continuity)
        commit_soulbond_pair(session.engine, pair_id='owner:2', source_ref=first.ref,
            partner_ref=second.ref, controller='A')
        before = authoritative_state_hash(session.state)
        with self.assertRaises(SoulbondError):
            commit_soulbond_pair(session.engine, pair_id='owner:3', source_ref=third.ref,
                partner_ref=second.ref, controller='A')
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertIsNone(third.source_continuity)

    def test_real_control_continuity_expires_pair_after_control_is_restored(self):
        from quorune.model import GameState
        from quorune.soulbond_state import commit_soulbond_pair, reconcile_soulbond_pairs
        session, first, second, third, enemy = self.fixture(702_095_012)
        commit_soulbond_pair(session.engine, pair_id='owner:4', source_ref=first.ref,
            partner_ref=second.ref, controller='A')
        session.engine.change_control(first.object_id, 'B', reason='pair continuity witness')
        self.assertEqual((), session.state.soulbond_pairs)
        session.engine.change_control(first.object_id, 'A', reason='control restored')
        self.assertEqual('A', first.controller)
        self.assertEqual(2, first.source_continuity.control_epoch)
        self.assertFalse(reconcile_soulbond_pairs(session.engine))
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertEqual([], session.state.to_dict()['soulbond_pairs'])
        self.assertEqual((), GameState.from_dict(session.state.to_dict()).soulbond_pairs)
        self.assertFalse(reconcile_soulbond_pairs(session.engine))

    def test_other_entry_proposal_is_pinned_to_the_entered_creature(self):
        from quorune.soulbond_state import commit_soulbond_pair
        from quorune.soulbond_model import SoulbondError
        session, first, second, third, enemy = self.fixture(702_095_013)
        before = authoritative_state_hash(session.state)
        with self.assertRaises(SoulbondError):
            commit_soulbond_pair(session.engine, pair_id='owner:5', source_ref=first.ref,
                partner_ref=second.ref, controller='A', entered_ref=third.ref)
        self.assertEqual(before, authoritative_state_hash(session.state))
        pair = commit_soulbond_pair(session.engine, pair_id='owner:5', source_ref=first.ref,
            partner_ref=third.ref, controller='A', entered_ref=third.ref)
        self.assertEqual(third.logical_object_id, pair.second.identity.logical_object_id)

    def test_choice_snapshot_decline_and_pinned_intent_use_the_same_candidates(self):
        from types import SimpleNamespace
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.soulbond import SoulbondPairChoiceHandler
        from quorune.semantic_choices.context import SemanticChoiceContext
        from quorune.semantic_runtime.intents import IntentPlan
        from quorune.semantic_runtime.executor import execute_intent_plan
        from quorune.soulbond_model import SoulbondOpportunity
        session, first, second, third, enemy = self.fixture(702_095_014)
        effect = SoulbondOpportunity('A', first.ref, first.logical_object_id, 'source_entry').to_effect()
        query = session.engine._semantic_choice_query('A', effect=effect, source_ref=first.ref)
        self.assertEqual({second.ref, third.ref}, set(query.choice_candidate_refs()))
        context = SemanticChoiceContext(actor='A', stack_ref='pair-trigger-1', stack_controller='A',
            stack_label='Soulbond', source_ref=first.ref, card_ref=first.ref,
            semantic_program_id='test:soulbond', semantic_program_version=1, query=query)
        handler = SoulbondPairChoiceHandler()
        prepared = handler.prepare(effect, context)
        self.assertIsNotNone(prepared.request)
        continuation = SimpleNamespace(effect=FrozenMap(effect), stack_ref=context.stack_ref)
        before = authoritative_state_hash(session.state)
        self.assertFalse(handler.complete(continuation, {'cards':[]}, query).intents)
        self.assertEqual(before, authoritative_state_hash(session.state))
        completion = handler.complete(continuation, {'cards':[second.ref]}, query)
        self.assertEqual(1, len(completion.intents))
        execute_intent_plan(session.engine, IntentPlan(operation=handler.operation,
            handler_id=handler.handler_id, intents=completion.intents))
        self.assertEqual(second.logical_object_id, session.state.soulbond_pairs[0].second.identity.logical_object_id)

    def test_choice_rejects_stale_source_or_entered_identity_without_initializing_history(self):
        from types import SimpleNamespace
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.soulbond import SoulbondPairChoiceHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.soulbond_model import SoulbondOpportunity
        session, first, second, third, enemy = self.fixture(702_095_015)
        effect = SoulbondOpportunity('A', first.ref, first.logical_object_id,
            'other_entry', third.ref, third.logical_object_id).to_effect()
        continuation = SimpleNamespace(effect=FrozenMap(effect), stack_ref='pair-trigger-2')
        for change in ({'source_identity':'stale-source'}, {'entered_identity':'stale-entrant'}):
            stale = {**effect, **change}
            query = session.engine._semantic_choice_query('A', effect=stale, source_ref=first.ref)
            self.assertEqual((), query.choice_candidate_refs())
            before = authoritative_state_hash(session.state)
            with self.assertRaises(SemanticChoiceError):
                SoulbondPairChoiceHandler().complete(
                    SimpleNamespace(effect=FrozenMap(stale), stack_ref=continuation.stack_ref),
                    {'cards':[third.ref]}, query)
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertIsNone(first.source_continuity)
