"""Token exclusion is a conjunctive public predicate, independent of copied text."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
import test_fixed_copy_type_exceptions as copies
from test_bound_effect_programs import record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.compiler.direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.permanent_predicate_capability_shapes import direct_target_predicate_capabilities
from scripts.build_test_database import build_fixture_database

CAPABILITY = 'target.permanent.characteristic_predicate'


class NontokenTargetCompilerTests(unittest.TestCase):
    def test_nontoken_scopes_all_type_branches_without_erasing_other_qualifiers(self):
        for text, types, excluded in (('target nontoken creature', ('creature',), ()),
                ('target nontoken artifact or creature', ('artifact', 'creature'), ()),
                ('target nontoken permanent', (), ()),
                ('another target nonland, nontoken permanent', (), ('land',)),
                ('target nontoken nonland permanent', (), ('land',))):
            with self.subTest(text=text):
                spec = direct_permanent_target_spec(text)
                self.assertIsNotNone(spec)
                self.assertIs(spec.token, False)
                self.assertEqual(types, spec.types_any)
                self.assertEqual(excluded, spec.types_none)
                self.assertEqual(text.startswith('another'), spec.source_exclusion)
                self.assertEqual(spec, DirectPermanentTargetSpec.from_target_schema(spec.to_target_schema()))
                self.assertIn(CAPABILITY, direct_target_predicate_capabilities(spec.to_target_schema()))
        old = direct_permanent_target_spec('target nontoken artifact').to_target_schema()
        self.assertEqual({'zones':['battlefield'],'categories':['permanent'],'count':1,
                          'types_any':['artifact'],'token':False}, old)
        for flag in (0, 1, 'false', [], {}):
            with self.assertRaises(ValueError): DirectPermanentTargetSpec(token=flag)
        for text in ('target nontoken token', 'target nontoken token creature',
                     'target nontoken nontoken artifact', 'target nontoken creature from another game',
                     'target nonland, nontoken, nonsnow permanent'):
            self.assertIsNone(direct_permanent_target_spec(text), text)

    def test_shared_effect_owners_and_contexts_require_the_existing_characteristic_capability(self):
        registry = load_default_capability_registry()
        for body in ('Destroy target nontoken creature.', 'Exile target nontoken permanent.',
                     "Return target nontoken creature to its owner's hand.",
                     'Put a +1/+1 counter on target nontoken creature.'):
            for prefix, kind in (('', 'Instant'), ('When this creature enters, ', 'Creature — Human'),
                                 ('{2}: ', 'Creature — Human')):
                ir = compile_oracle_card(record(prefix+body, type_line=kind), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertIn(CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['predicate withdrawn'])
        withdrawn = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual('exact', compile_oracle_card(record('Destroy target nontoken creature.'), capability_registry=withdrawn).status)
        self.assertEqual('exact', compile_oracle_card(record('Draw a card.'), capability_registry=withdrawn).status)


class NontokenTargetNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish
    choose_copy = copies.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        (folder/'basic.json').write_text(json.dumps(base), encoding='utf-8')
        path = folder/'nontoken.sqlite3'
        build_fixture_database([folder/'basic.json', ROOT/'tests/fixtures/nontoken-target-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Nontoken targets', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def scene(self, name, *, zone='hand', before=None):
        s = self.session(1111009+len(name))
        source = self.add(s.engine, name, zone=zone)
        source.acquired_control_turn_count = -1
        body = self.add(s.engine, 'Grizzly Bears', seat='B', ref='nontoken-body')
        own = self.add(s.engine, 'Grizzly Bears', ref='nontoken-own')
        land = self.add(s.engine, 'Forest', seat='B', ref='nontoken-land')
        ref, = s.engine.create_token('B', name=body.printed_name, copy_of=body.ref)
        token = next(c for c in s.state.cards.values() if c.ref == ref)
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='nontoken-private-C')
        if before is not None: before(s, source, body, own, land, token)
        s.state.config.semantic_policy = 'trusted_only'
        self.assert_closed(s, source)
        self.prepare(s, mana={'C':20, **{c:10 for c in 'WUBRG'}})
        self.checkpoint(s)
        return s, source, body, own, land, token, private

    def command(self, s, source, targets, *, face=None, forbidden=()):
        self.priority(s, 'A')
        rows = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        row = next(row for row in rows if row.get('card') == source.ref and
                   (row.get('face') == face if face else row['id'] == 'cast:'+source.ref))
        schema = row.get('target_schema') or row['cost_options'][0]['target_schema']
        for ref in targets: self.assertIn(ref, schema['legal_refs'])
        for ref in forbidden: self.assertNotIn(ref, schema['legal_refs'])
        command = {'action_id':row['id'], 'targets':list(targets), 'pay':'auto'}
        if face: command['face'] = face
        if row.get('cost_options'): command['cost_option'] = row['cost_options'][0]['id']
        return command

    def checked_cast(self, s, source, target, token, *, face=None):
        command = self.command(s, source, [target.ref], face=face, forbidden=(token.ref,))
        self.reject(s, 'D', command)
        self.reject(s, 'A', {**command, 'targets':[token.ref]})
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_flicker_accepts_a_card_copying_a_token_and_rejects_the_identical_token(self):
        extra = {}
        def before(s, source, body, own, land, token): extra['clone'] = self.add(s.engine, 'Clone', zone='hand')
        s, source, body, own, land, token, private = self.scene('Flicker', before=before)
        clone = extra['clone']
        self.assert_closed(s, clone)
        self.cast(s, 'A', clone)
        s = self.choose_copy(s, token)
        self.assertFalse(s.state.cards[clone.object_id].is_token)
        self.assertTrue(s.state.cards[token.object_id].is_token)
        old_identity = s.state.cards[clone.object_id].logical_object_id
        self.checked_cast(s, source, clone, token)
        s = self.choose_copy(s, token)
        self.assertNotEqual(old_identity, s.state.cards[clone.object_id].logical_object_id)
        self.assertFalse(s.state.cards[clone.object_id].is_token)
        self.finish(s, private)

    def test_actual_adventure_exiles_only_the_nontoken_card_and_returns_it_at_the_next_end_step(self):
        s, source, body, own, land, token, private = self.scene('Twining Twins // Swift Spiral')
        identity = s.state.cards[body.object_id].logical_object_id
        self.checked_cast(s, source, body, token, face='Swift Spiral')
        self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[body.object_id].zone)
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[token.object_id].zone)
        s = self.replay(s, load=True)
        self.until(s, lambda game: game.state.phase == 'ending' and game.state.step == 'end_step'
                   and game.state.cards[body.object_id].zone == 'battlefield')
        self.assertEqual('B', s.state.cards[body.object_id].controller)
        self.assertNotEqual(identity, s.state.cards[body.object_id].logical_object_id)
        self.finish(s, private)

    def test_actual_bounce_returns_the_card_to_its_owner_before_the_ring_chooses_a_bearer(self):
        def before(s, source, body, own, land, token): body.owner = 'D'
        s, source, body, own, land, token, private = self.scene('Soothing of Sm\u00e9agol', before=before)
        self.checked_cast(s, source, body, token)
        self.assertIsNotNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[body.object_id].zone)
        self.assertIn(body.object_id, s.state.players['D'].zones['hand'])
        result = s.act('pilot:A', {'action_id':'choose', 'objects':[own.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.finish(s, private)

    def test_actual_thor_excludes_an_equipment_token_and_draws_for_the_returning_equipment_card(self):
        extra = {}
        def before(s, source, body, own, land, token):
            self.add(s.engine, 'Fervor')
            extra['equipment'] = self.add(s.engine, 'Bonesplitter')
            ref, = s.engine.create_token('A', name=extra['equipment'].printed_name, copy_of=extra['equipment'].ref)
            extra['token'] = next(c for c in s.state.cards.values() if c.ref == ref)
        s, source, body, own, land, token, private = self.scene('The Mighty Thor, Jane Foster', before=before)
        self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s))
        hand = len(s.state.players['A'].zones['hand'])
        self.attack(s, [(source, 'B')])
        self.assertIsNotNone(self.resolve(s))
        legal = s.packet('pilot:A', full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertIn(extra['equipment'].ref, legal)
        for ref in (token.ref, extra['token'].ref):
            self.assertNotIn(ref, legal)
            self.reject(s, 'A', {'action_id':'choose', 'targets':[ref]})
        self.target(s, 'A', [extra['equipment'].ref])
        self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[extra['equipment'].object_id].tapped)
        self.assertEqual(hand+1, len(s.state.players['A'].zones['hand']))
        self.finish(s, private)

    def test_actual_cast_and_tap_triggers_keep_nonland_token_and_source_exclusions_through_delayed_return(self):
        for name in ('Wiccan, Rising Magician', 'Mistmeadow Vanisher'):
            with self.subTest(name=name):
                extra = {}
                def before(s, source, body, own, land, token):
                    body.owner = 'D'
                    extra['action'] = self.add(s.engine, 'Giant Growth' if name.startswith('Wiccan') else 'Icy Manipulator',
                                              zone='hand' if name.startswith('Wiccan') else 'battlefield')
                s, source, body, own, land, token, private = self.scene(name, zone='battlefield', before=before)
                if name.startswith('Wiccan'): self.cast(s, 'A', extra['action'], [own.ref])
                else:
                    self.priority(s, 'A')
                    row = next(r for r in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                               if r['id'].startswith('activate:'+extra['action'].ref+':'))
                    result = s.act('pilot:A', {'action_id':row['id'], 'targets':[source.ref], 'pay':'auto'})
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNotNone(self.resolve(s))
                legal = s.packet('pilot:A', full=True)['decision']['ctx']['target_schema']['legal_refs']
                self.assertIn(body.ref, legal)
                for forbidden in (token.ref, land.ref, *((source.ref,) if name.startswith('Wiccan') else ())):
                    self.assertNotIn(forbidden, legal)
                    self.reject(s, 'A', {'action_id':'choose', 'targets':[forbidden]})
                self.target(s, 'A', [body.ref])
                self.assertIsNone(self.resolve(s))
                self.assertEqual('exile', s.state.cards[body.object_id].zone)
                s = self.replay(s, load=True)
                self.until(s, lambda game: game.state.phase == 'ending' and game.state.step == 'end_step'
                           and game.state.cards[body.object_id].zone == 'battlefield')
                self.assertEqual('D', s.state.cards[body.object_id].controller)
                self.assertIn(body.object_id, s.state.players['D'].zones['battlefield'])
                self.finish(s, private)

    def test_actual_old_adventure_target_does_not_follow_the_same_card_after_bounce_and_recast(self):
        extra = {}
        def before(s, source, body, own, land, token):
            extra['bounce'] = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
            self.add(s.engine, 'Vedalken Orrery', seat='B')
            s.state.players['B'].mana_pool.update({'U':1, 'C':1, 'G':1})
        s, source, body, own, land, token, private = self.scene('Twining Twins // Swift Spiral', before=before)
        self.checked_cast(s, source, body, token, face='Swift Spiral')
        old = s.state.cards[body.object_id].logical_object_id
        self.cast(s, 'B', extra['bounce'], [body.ref])
        self.resolve_top(s)
        self.cast(s, 'B', body)
        self.resolve_top(s)
        self.assertNotEqual(old, s.state.cards[body.object_id].logical_object_id)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[body.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.delayed_triggers)
        self.finish(s, private)

    def test_actual_token_omission_mutant_is_killed_by_an_otherwise_identical_token(self):
        original = DirectPermanentTargetSpec.to_target_schema
        def omitted(spec):
            schema = original(spec)
            if spec.token is False: schema.pop('token')
            return schema
        with patch.object(DirectPermanentTargetSpec, 'to_target_schema', omitted):
            s, source, body, own, land, token, private = self.scene('Flicker')
            command = self.command(s, source, [token.ref])
            with self.assertRaisesRegex(AssertionError, 'True is not false'): self.reject(s, 'A', command)
