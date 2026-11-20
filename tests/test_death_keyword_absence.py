"""Last-known creature characteristics and complete Wilhelt gameplay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.death_keyword_absence import DEATH_KEYWORD_ABSENCE_COVERAGE, DeathKeywordAbsence
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WILHELT = 'Wilhelt, the Rotcleaver'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'
CONDITION = 'trigger.condition.fixed_public_state'
DEATH = 'trigger.event.normalized_zone_change'


class DeathKeywordAbsenceTests(unittest.TestCase):
    def test_closed_codec_and_keyword_facts_fail_closed(self):
        spec = DeathKeywordAbsence('zombie')
        self.assertEqual(spec, DeathKeywordAbsence.from_dict(spec.to_dict()))
        for change in ({'subtype': 'Zombie'}, {'subtype': 'fake'}, {'keyword': 'flying'},
                {'schema_version': True}, {'state': 'current'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                DeathKeywordAbsence.from_dict({**spec.to_dict(), **change})

        fact = {'from': 'battlefield', 'to': 'graveyard', 'card': 'C4',
            'card_object_identity': 'card4@1', 'previous_controller': 'A',
            'types': ['creature'], 'subtypes': ['zombie'], 'keywords': ['flying']}
        self.assertTrue(spec.holds(fact, controller='A'))
        self.assertFalse(spec.holds({**fact, 'event': 'creature.dies', 'keywords': ['decayed']}, controller='A'))
        for change in ({'card_object_identity': 'card4'}, {'previous_controller': 'B'}, {'to': 'exile'},
                {'subtypes': ['human']}, {'keywords': None}, {'keywords': ['Decayed']}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                spec.holds({**fact, **change}, controller='A')

    def test_original_wilhelt_keeps_both_abilities_and_the_normalized_death_owner(self):
        reg = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'death-keyword.sqlite3'
            build_fixture_database([
                FIXTURES / 'controller-creature-keyword-absence-cards.json',
                FIXTURES / 'death-keyword-absence-cards.json',
            ], path)
            with CardDatabase(path) as db:
                card = db.lookup(WILHELT, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=reg, capability_profile=PROFILE)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                death, end_step = ir.faces[0].nodes
                self.assertEqual(('creature.dies', 'step.begin'), (death.event, end_step.event))
                self.assertEqual(card.oracle_text.splitlines(),
                    [card.oracle_text[n.span.start:n.span.end] for n in (death, end_step)])
                scope, predicate = death.event_condition['all']
                spec = DeathKeywordAbsence.from_dict(predicate['condition'])
                self.assertEqual(DeathKeywordAbsence('zombie'), spec)
                self.assertEqual(spec.occurrence_condition(), scope)
                self.assertTrue({CONDITION, DEATH, 'combat.trigger.decayed'} <= set(death.capability_dependencies))
                self.assertTrue({'choice.affected_player.fixed_sacrifice', 'zone.draw.library_to_hand'}
                    <= set(end_step.capability_dependencies))
                ready, sem = register_card(db, reg, WILHELT)
                self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
                program, = [p for p in sem.programs_for_oracle(card.oracle_id) if p.event == 'creature.dies']
                self.assertIn(DEATH_KEYWORD_ABSENCE_COVERAGE, program.coverage)

                for original, change in (('another Zombie', 'a Zombie'), ('Zombie you control', 'Zombie'),
                        ("didn't have decayed", "didn't have flying"), ('decayed,', 'decayed or haste,')):
                    with self.subTest(excluded=change):
                        altered = replace(card, oracle_text=card.oracle_text.replace(original, change, 1))
                        candidate = compile_oracle_card(altered, capability_registry=reg, capability_profile=PROFILE)
                        self.assertNotEqual('exact', candidate.status)

                for field, change in (
                    ('event', 'permanent.leave'),
                    ('event_condition', predicate),
                    ('event_condition', scope),
                    ('coverage', [c for c in program.coverage if c != DEATH_KEYWORD_ABSENCE_COVERAGE]),
                ):
                    with self.subTest(raw=field):
                        altered = replace(program, **{field: change})
                        binding = bind_semantic_program_runtime(altered, capability_registry=reg, profile=PROFILE)
                        self.assertFalse(binding['strict'])
                        self.assertTrue(any('runtime_event_subscriptions:invalid:' in s for s in binding['blockers']),
                            binding['blockers'])


class DeathKeywordAbsenceNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'death-keyword-native.sqlite3'
        build_fixture_database([
            basic, FIXTURES / 'targeted-graveyard-exile-cards.json',
            FIXTURES / 'controller-creature-keyword-absence-cards.json',
            FIXTURES / 'death-keyword-absence-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Sealed death keyword', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    @staticmethod
    def own_tokens(game):
        return [card for card in game.state.cards.values()
            if card.zone == 'battlefield' and card.controller == 'A' and card.is_token
            and card.printed_name == 'Zombie Token']

    def paid(self, game, source, targets=(), *, seat='A'):
        self.cast(game, seat, source, targets)
        self.assertIsNone(self.resolve(game))

    def privacy_replay(self, game, hidden):
        for seat in game.engine.active_seats:
            if seat != hidden.owner:
                packet = json.dumps(game.packet('pilot:' + seat, full=True))
                self.assertNotIn(hidden.ref, packet)
                self.assertNotIn(hidden.printed_name, packet)
        self.replay(game, load=True)

    def test_paid_mass_death_uses_controller_and_keywords_before_both_creatures_leave(self):
        game = self.session(60310710)
        source = self.add(game.engine, WILHELT, zone='hand')
        corpse = self.add(game.engine, 'Walking Corpse', zone='hand')
        maker = self.add(game.engine, 'Rotten Reunion', zone='hand')
        destroy = self.add(game.engine, 'Damnation', zone='hand')
        foreign = self.add(game.engine, 'Walking Corpse', seat='B', ref='sealed-death-foreign-zombie')
        nonzombie = self.add(game.engine, 'Grizzly Bears', ref='sealed-death-nonzombie')
        hidden = self.add(game.engine, 'Humble', seat='D', zone='hand', ref='sealed-death-secret')
        for spell in (source, corpse, maker, destroy):
            self.assert_closed(game, spell)
        self.prepare(game, mana={'C': 5, 'U': 1, 'B': 5})
        self.checkpoint(game)
        self.paid(game, source)
        self.paid(game, corpse)
        self.paid(game, maker)
        decayed, = self.own_tokens(game)
        self.assertIn('decayed', game.engine._combat_keywords(decayed))
        dying_ref = game.state.cards[corpse.object_id].ref
        dying_identity = game.state.cards[corpse.object_id].logical_object_id
        self.cast(game, 'A', destroy)
        self.resolve_top(game)
        for card in (source, corpse, nonzombie, foreign):
            self.assertEqual('graveyard', game.state.cards[card.object_id].zone)
        self.assertEqual('outside', game.state.cards[decayed.object_id].zone)
        self.assertEqual(1, len(game.state.stack))
        trigger, = game.state.stack
        self.assertEqual((source.object_id, 'A', 'creature.dies', dying_ref, dying_identity), (
            trigger.source_object_id, trigger.controller, trigger.context['event'],
            trigger.context['card'], trigger.context['card_object_identity'],
        ))
        self.assertNotIn('decayed', trigger.context['keywords'])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        replacement, = self.own_tokens(game)
        self.assertNotEqual(decayed.object_id, replacement.object_id)
        self.assertIn('decayed', game.engine._combat_keywords(replacement))
        self.privacy_replay(game, hidden)

    def test_paid_ability_loss_changes_the_dying_tokens_decayed_membership(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                game = self.session(60310720 + remove)
                source = self.add(game.engine, WILHELT, zone='hand')
                maker = self.add(game.engine, 'Rotten Reunion', zone='hand')
                shock = self.add(game.engine, 'Shock', zone='hand')
                self.add(game.engine, 'Mountain', ref='sealed-death-shock-mana')
                humility = self.add(game.engine, 'Humble', seat='B', zone='hand')
                self.add(game.engine, 'Plains', seat='B', ref='sealed-death-font-first')
                self.add(game.engine, 'Plains', seat='B', ref='sealed-death-font-second')
                for spell in (source, maker, shock, humility):
                    self.assert_closed(game, spell)
                self.prepare(game, mana={'C': 2, 'U': 1, 'B': 2})
                self.checkpoint(game)
                self.paid(game, source)
                self.paid(game, maker)
                dying, = self.own_tokens(game)
                if remove:
                    self.paid(game, humility, [dying.ref], seat='B')
                self.assertEqual(not remove, 'decayed' in game.engine._combat_keywords(game.state.cards[dying.object_id]))
                self.cast(game, 'A', shock, [dying.ref])
                self.resolve_top(game)
                self.assertEqual('outside', game.state.cards[dying.object_id].zone)
                self.assertEqual(int(remove), self.trigger_count(game, source))
                if remove:
                    self.resolve_top(game)
                self.assertEqual(int(remove), len(self.own_tokens(game)))
                self.replay(game, load=True)

    def test_paid_optional_end_step_preserves_legal_sacrifice_and_private_draw(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                game = self.session(60310730 + pay)
                source = self.add(game.engine, WILHELT, zone='hand')
                corpse = self.add(game.engine, 'Walking Corpse', zone='hand')
                foreign = self.add(game.engine, 'Walking Corpse', seat='B', ref='sealed-death-foreign-zombie')
                nonzombie = self.add(game.engine, 'Grizzly Bears')
                hidden = self.add(game.engine, 'Humble', seat='D', zone='hand', ref='sealed-payment-secret')
                self.assert_closed(game, source)
                self.prepare(game, mana={'C': 3, 'U': 1, 'B': 2})
                self.checkpoint(game)
                self.paid(game, source)
                self.paid(game, corpse)
                self.until(game, lambda state: state.state.step == 'end_step')
                self.assertEqual('semantic.choice', self.resolve(game).kind)
                offer = game.packet('pilot:A', full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
                self.assertEqual({source.ref, corpse.ref}, set(offer['legal_refs']))

                decision = {'action_id': 'choose', 'cards': [corpse.ref] if pay else []}
                before = authoritative_state_hash(game.state)
                for seat, invalid in (('B', decision),
                        ('A', {'action_id': 'choose', 'cards': [foreign.ref]}),
                        ('A', {'action_id': 'choose', 'cards': [nonzombie.ref]})):
                    self.assertFalse(game.act('pilot:' + seat, invalid).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                previous_hand = set(game.state.players['A'].zones['hand'])
                game = self.replay(game, load=True)
                result = game.act('pilot:A', decision)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(int(pay), self.trigger_count(game, source))
                drawn = set(game.state.players['A'].zones['hand']) - previous_hand
                self.assertEqual(int(pay), len(drawn))
                if pay:
                    draw_ref = game.state.cards[next(iter(drawn))].ref
                    for seat in 'BCD':
                        self.assertNotIn(draw_ref, json.dumps(game.packet('pilot:' + seat, full=True)))
                    self.resolve_top(game)
                self.assertEqual('graveyard' if pay else 'battlefield', game.state.cards[corpse.object_id].zone)
                self.assertEqual(int(pay), len(self.own_tokens(game)))
                self.privacy_replay(game, hidden)


    def test_ignoring_the_sealed_decayed_keyword_is_detected_at_paid_mass_death(self):
        from unittest.mock import patch

        original = DeathKeywordAbsence.holds

        def omit_decayed(spec, context, *, controller):
            return original(spec, {**context, 'keywords': []}, controller=controller)

        with patch.object(DeathKeywordAbsence, 'holds', omit_decayed):
            with self.assertRaisesRegex(AssertionError, r'1 != [02]'):
                self.test_paid_mass_death_uses_controller_and_keywords_before_both_creatures_leave()

    def test_verified_death_condition_and_sacrifice_withdrawals_block_complete_wilhelt(self):
        from copy import deepcopy

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (WILHELT, 'Walking Corpse', 'Rotten Reunion')
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready['strict_capability_ready'], ready['blockers'])

        for missing in (CONDITION, DEATH, 'choice.affected_player.fixed_sacrifice'):
            with self.subTest(withdrawn=missing):
                rows = deepcopy(source)
                next(row for row in rows['capabilities'] if row['id'] == missing).update(
                    status='blocked', blockers=['Exact death condition family owner withdrawn'])
                registry = verified(rows)
                closure = registry.closure((missing,), profile=PROFILE)
                self.assertIn('status:' + missing + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in value for value in closure.blockers))
                blocked = {WILHELT}
                if missing == DEATH:
                    blocked.add('Rotten Reunion')  # Flashback independently needs the normalized zone owner.
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready['strict_capability_ready'], (name, ready['blockers']))
