"""CR903.8/608.2h: game-wide commander casts at live and resolution timing."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import CharacteristicQuantityScope as Scope, CharacteristicQuantitySpec, characteristic_quantity_capabilities
from quorune.commander_cast_count_model import COMMANDER_CAST_COUNT_CAPABILITY
from quorune.commander_cast_counts import controller_commander_cast_count
from quorune.compiler.commander_cast_quantity_templates import commander_cast_quantity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from quorune.semantic_runtime.commander_cast_copies import COMMANDER_CAST_COPY_CAPABILITY, COMMANDER_CAST_COPY_KEY
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

NAMES = ["Commander's Insignia", 'Vanguard of the Restless', 'Captain Vargus Wrath', 'Jirina Kudro',
    'Jyoti, Moag Ancient', 'Empyrial Storm', 'Echo Storm', 'Fury Storm', 'Hatut Zeraze Strike Force']
FIXTURE = ROOT / 'tests/fixtures/commander-cast-quantity-cards.json'


class CommanderCastQuantityModelTests(unittest.TestCase):
    def test_commander_quantity_has_a_new_closed_version_without_changing_old_wire(self):
        from quorune.object_predicate import ObjectQuerySpec
        old = CharacteristicQuantitySpec(Scope.CONTROLLER_ZONE, query=ObjectQuerySpec(zones=('battlefield',)))
        self.assertEqual({'schema_version': 1, 'scope': 'controller_zone', 'query': old.query.to_dict(),
            'counter_name': None, 'exclude_source': False, 'exclude_attached_object': False}, old.to_dict())
        value = CharacteristicQuantitySpec(Scope.CONTROLLER_COMMANDER_CAST_COUNT, schema_version=8)
        self.assertEqual(value, CharacteristicQuantitySpec.from_dict(value.to_dict()))
        self.assertEqual((COMMANDER_CAST_COUNT_CAPABILITY,), characteristic_quantity_capabilities(value))
        for change in ({'schema_version': 1}, {'schema_version': True}, {'scope': 'controller_zone'},
                {'query': old.query.to_dict()}, {'offset': 1}, {'multiplier': 2}, {'exclude_source': True},
                {'counter_name': 'charge'}, {'terms': []}, {'invented': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                CharacteristicQuantitySpec.from_dict({**value.to_dict(), **change})

    def test_commander_reader_sums_partners_and_rejects_corrupt_ledger_values(self):
        player = SimpleNamespace(commander_casts={'partner-one': 2, 'partner-two': 1})
        state = SimpleNamespace(players={'A': player, 'B': SimpleNamespace(commander_casts={'other': 7})})
        self.assertEqual(3, controller_commander_cast_count(state, 'A'))
        self.assertEqual(7, controller_commander_cast_count(state, 'B'))
        for ledger in ({'x': True}, {'x': -1}, {'x': 1.0}, {1: 1}, {'': 1}, []):
            player.commander_casts = ledger
            with self.subTest(ledger=ledger), self.assertRaises(ValueError):
                controller_commander_cast_count(state, 'A')
        player.commander_casts = {}
        self.assertEqual(0, controller_commander_cast_count(state, 'A'))
        with self.assertRaises(ValueError): controller_commander_cast_count(state, 'C')

    def test_quantity_grammar_consumes_only_command_zone_game_counts(self):
        for text in ("time you've cast your commander from the command zone this game",
                "time you've cast a commander from the command zone this game"):
            self.assertEqual(8, commander_cast_quantity(text).schema_version)
        for text in ("time you've cast your commander this turn", "time you've cast your commander from your hand",
                "time an opponent has cast a commander from the command zone this game",
                "time you've cast your commander from the command zone this game and drawn a card"):
            self.assertIsNone(commander_cast_quantity(text))
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        from quorune.rules.token_creation_capability_shapes import token_definition_payload_dependencies
        effect = fixed_token_creation_effect_template('Create a 1/1 green Forest Dryad land creature token.').effect
        self.assertEqual('Token Land Creature — Forest Dryad', effect['characteristics']['type_line'])
        self.assertEqual('Forest Dryad Token', effect['name'])
        self.assertIn('mana.intrinsic.basic_land_type', token_definition_payload_dependencies(effect))

    def test_scalar_origin_requires_the_same_runtime_quantity_authority(self):
        value = ScalarEffectAmountSpec(ScalarAmountOrigin.CONTROLLER_COMMANDER_CAST_COUNT)
        self.assertEqual(value, ScalarEffectAmountSpec.from_dict(value.to_dict()))
        self.assertIn(COMMANDER_CAST_COUNT_CAPABILITY, value.referenced_quantity_capabilities)
        for change in ({'characteristic': 'power'}, {'history_fact': 'spells_cast'}, {'schema_version': 2},
                {'coefficient': True}, {'counter_name': 'charge'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ScalarEffectAmountSpec.from_dict({**value.to_dict(), **change})

    def test_copy_kind_is_explicit_and_reads_at_resolution_without_mutating_old_storm(self):
        from quorune.ability_fragments import SpellCastKeywordTriggerKind as Kind, SpellCastKeywordTriggerSpec
        from quorune.commander_cast_copies import validated_commander_cast_copy_trigger
        from quorune.model import StackItem
        from quorune.storm import validated_storm_trigger, STORM_SEMANTIC_KEY
        host = SimpleNamespace(state=SimpleNamespace(players={'A': SimpleNamespace(commander_casts={'one': 1})}))
        item = StackItem(stack_id='trigger', ref='S1', kind='triggered_ability', controller='A', label='Copy',
            semantic_key=COMMANDER_CAST_COPY_KEY, context={'commander_cast_spec': SpellCastKeywordTriggerSpec(Kind.COMMANDER_CAST_COPY).to_dict(),
                'commander_cast_instance': 1, 'copy_template': {'label': 'Original', 'copy_permanent_spell': False,
                    'cast_text_copy_characteristics': {'name': 'Original', 'type_line': 'Sorcery'}}})
        self.assertEqual(1, validated_commander_cast_copy_trigger(host, item)[0])
        host.state.players['A'].commander_casts['two'] = 2
        count, template = validated_commander_cast_copy_trigger(host, item)
        self.assertEqual(3, count); self.assertFalse(template['commander_copy_new_targets'])
        from quorune.commander_cast_copies import completed_commander_cast_copy_spec
        frozen = {'copy_count': count, 'copy_template': deepcopy(template)}
        host.state.players['A'].commander_casts['two'] = 9
        with patch('quorune.commander_cast_copies.controller_commander_cast_count', side_effect=AssertionError('late recount')):
            self.assertEqual((3, template), completed_commander_cast_copy_spec(item, frozen))
        for change in ({'copy_count': True}, {'copy_count': -1}, {'copy_template': {}}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                completed_commander_cast_copy_spec(item, {**frozen, **change})
        template['label'] = 'Mutated'; self.assertEqual('Original', item.context['copy_template']['label'])
        old = replace(item, semantic_key=STORM_SEMANTIC_KEY, context={'copy_count': 4, 'copy_template': {'label': 'Old'}})
        self.assertEqual(4, validated_storm_trigger(old)[0])
        for change in ({'commander_cast_instance': True}, {'copy_count': 1},
                {'commander_cast_spec': SpellCastKeywordTriggerSpec(Kind.STORM).to_dict()}, {'copy_template': None}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validated_commander_cast_copy_trigger(host, replace(item, context={**item.context, **change}))

    def test_first_commander_only_mutant_is_killed_by_the_partner_contract(self):
        from quorune import commander_cast_counts as owner
        state = SimpleNamespace(players={'A': SimpleNamespace(commander_casts={'one': 1, 'two': 2})})
        def witness(): self.assertEqual(3, owner.controller_commander_cast_count(state, 'A'))
        witness()
        with patch.object(owner, 'controller_commander_cast_count', return_value=1), self.assertRaises(AssertionError): witness()


class CommanderCastQuantityCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name) / 'commander-casts.sqlite3'
        build_fixture_database([FIXTURE], path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def test_all_nine_originals_have_complete_normal_admission_and_source_spans(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        semantics = SemanticRegistry(); cards = [self.db.lookup(n, fuzzy=False) for n in NAMES]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional', capability_registry=self.registry,
            capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes: self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'], (card.name, bound['blockers']))
                from quorune.card_programs.binding import bind_semantic_program_runtime
                for semantic in semantics.programs_for_oracle(card.oracle_id):
                    for missing in (COMMANDER_CAST_COUNT_CAPABILITY, COMMANDER_CAST_COPY_CAPABILITY):
                        if missing not in semantic.capability_dependencies: continue
                        dependencies = tuple(c for c in semantic.capability_dependencies if c != missing)
                        changed = replace(semantic, capability_dependencies=dependencies,
                            capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
                        check = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                        self.assertFalse(check['strict'])
                        self.assertIn('capability:undeclared_runtime_dependency:' + missing, check['blockers'])


    def test_verified_count_or_copy_withdrawal_blocks_affected_cards_and_preserves_font(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for owner, names in ((COMMANDER_CAST_COUNT_CAPABILITY, NAMES), (COMMANDER_CAST_COPY_CAPABILITY, NAMES[5:])):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            for name in names:
                self.assertNotEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=registry,
                    capability_profile='commander_review').status)
            self.assertEqual('exact', compile_oracle_card(self.db.lookup('Font of Magic'), capability_registry=registry,
                capability_profile='commander_review').status)


class CommanderCastQuantityNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name) / 'commander-native.sqlite3'
        build_fixture_database([FIXTURE], path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Commander casts', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def tokens(self, session, *, seat='A', subtype=None):
        return [c for c in session.state.cards.values() if not c.is_card_object and c.zone == 'battlefield' and c.controller == seat
            and (subtype is None or subtype.casefold() in session.engine._type_parts(session.engine._effective_card_data(c)['type_line'])[1])]

    def test_actual_insignia_counts_pending_partner_casts_and_hand_casts_do_not_add(self):
        s = self.session(690001); anthem = self.add(s.engine, "Commander's Insignia"); bear = self.add(s.engine, 'Grizzly Bears')
        partner = self.add(s.engine, 'Surrak Dragonclaw', zone='command'); partner.is_commander = True
        hand = self.add(s.engine, 'Isamaru, Hound of Konda', zone='hand'); hand.is_commander = True
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-cast-private-one')
        self.assert_closed(s, anthem); self.prepare(s, mana={'C': 2, 'R': 1, 'G': 1, 'U': 1, 'W': 1}); self.checkpoint(s)
        self.cast_for(s, 'A', partner, reserve={'W': 1}); self.assertEqual(1, controller_commander_cast_count(s.state, 'A'))
        self.assertEqual(3, s.engine._numeric_stat(bear.object_id, 'power'))
        self.assertIsNone(self.resolve(s)); self.cast(s, 'A', hand); self.assertIsNone(self.resolve(s))
        self.assertEqual(1, controller_commander_cast_count(s.state, 'A')); self.finish(s, private)

    def test_actual_vanguard_and_insignia_recompute_for_current_controller(self):
        s = self.session(690002); anthem = self.add(s.engine, "Commander's Insignia"); spirit = self.add(s.engine, 'Vanguard of the Restless')
        a = self.add(s.engine, 'Lantern Spirit'); b = self.add(s.engine, 'Grizzly Bears', seat='B')
        gift = self.add(s.engine, 'Donate', zone='hand'); private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-cast-private-two')
        s.state.players['A'].commander_casts = {'one': 1, 'two': 1}; s.state.players['B'].commander_casts = {'three': 1}
        self.prepare(s, mana={'C': 2, 'U': 1}); self.checkpoint(s)
        self.assertEqual(6, s.engine._numeric_stat(a.object_id, 'power')); self.assertEqual(2, s.engine._numeric_stat(b.object_id, 'power'))
        self.cast(s, 'A', gift, ['B', anthem.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(4, s.engine._numeric_stat(a.object_id, 'power')); self.assertEqual(3, s.engine._numeric_stat(b.object_id, 'power'))
        self.assertIn('flying', s.engine._combat_keywords(spirit)); self.finish(s, private)

    def test_actual_jirina_includes_own_command_cast_and_zero_hand_cast_creates_no_tokens(self):
        for command in (False, True):
            s = self.session(690010 + command); card = self.add(s.engine, 'Jirina Kudro', zone='command' if command else 'hand')
            card.is_commander = command; private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-jirina-private')
            self.prepare(s, mana={'C': 1, 'R': 1, 'W': 1, 'B': 1}); self.checkpoint(s)
            self.cast(s, 'A', card); self.assertIsNone(self.resolve(s)); tokens = self.tokens(s, subtype='human')
            self.assertEqual(int(command), len(tokens))
            for token in tokens:
                self.assertEqual((3, 1), (s.engine._numeric_stat(token.object_id, 'power'), s.engine._numeric_stat(token.object_id, 'toughness')))
                self.assertIn('soldier', s.engine._type_parts(s.engine._effective_card_data(token)['type_line'])[1])
            self.finish(s, private)

    def test_actual_jyoti_creates_forest_land_creatures_with_mana_and_combat_sibling(self):
        s = self.session(690020); card = self.add(s.engine, 'Jyoti, Moag Ancient', zone='hand')
        haste = self.add(s.engine, 'Fervor', zone='hand')
        s.state.players['A'].commander_casts = {'one': 1, 'two': 1}
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-jyoti-private')
        self.prepare(s, mana={'C': 4, 'G': 1, 'U': 1, 'R': 1}); self.checkpoint(s)
        self.cast_for(s, 'A', card, reserve={'C': 2, 'R': 1}); self.assertIsNone(self.resolve(s))
        tokens = self.tokens(s, subtype='dryad'); self.assertEqual(2, len(tokens))
        for token in tokens:
            types, subtypes, supers = s.engine._type_parts(s.engine._effective_card_data(token)['type_line'])
            self.assertTrue({'land', 'creature'} <= types); self.assertIn('forest', subtypes); self.assertNotIn('basic', supers)
            self.assertEqual(1, s.engine._numeric_stat(token.object_id, 'power'))
            self.assertIn('intrinsic_forest', [a.ability_id for a in s.engine._activated_abilities(token)])
            action_id = 'activate:' + token.ref + ':intrinsic_forest'
            self.assertFalse(any(a['id'] == action_id for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
            self.reject(s, 'A', {'action_id': action_id})
        self.cast(s, 'A', haste); self.assertIsNone(self.resolve(s))
        token = s.state.cards[tokens[0].object_id]; action_id = 'activate:' + token.ref + ':intrinsic_forest'
        self.assertTrue(any(a['id'] == action_id for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
        self.reject(s, 'B', {'action_id': action_id})
        green = s.state.players['A'].mana_pool.get('G', 0)
        result = s.act('pilot:A', {'action_id': action_id}); self.assertTrue(result.ok, result.summary)
        self.assertEqual(green + 1, s.state.players['A'].mana_pool.get('G', 0)); self.assertTrue(s.state.cards[token.object_id].tapped)
        self.until(s, lambda x: x.state.phase == 'combat' and x.state.stack)
        self.assertIsNone(self.resolve(s))
        for token in tokens: self.assertEqual(3, s.engine._numeric_stat(token.object_id, 'power'))
        self.finish(s, private)

    def test_actual_echo_copies_retarget_independently_and_fault_rolls_back_partial_creation(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s = self.session(690060); card = self.add(s.engine, 'Echo Storm', zone='hand')
        ring = self.add(s.engine, 'Sol Ring', seat='B'); stone = self.add(s.engine, 'Mind Stone', seat='D')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-echo-private')
        s.state.players['A'].commander_casts = {'one': 1, 'two': 1}
        self.prepare(s, mana={'C': 3, 'U': 2}); self.checkpoint(s); self.cast(s, 'A', card, [ring.ref])
        decision = self.resolve(s); self.assertEqual('semantic.storm', decision.kind)
        command = {'action_id': 'choose', 'copy_targets': [[ring.ref], [stone.ref]]}
        self.reject(s, 'B', command); self.reject(s, 'A', {'action_id': 'choose', 'copy_targets': [[ring.ref]]})
        s = self.replay(s, load=True); create = s.engine._create_copy_object; calls = []
        def failed_second(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2: raise GameRuleError('injected second commander-copy creation failure')
            return create(*args, **kwargs)
        before = authoritative_state_hash(s.state)
        with patch.object(s.engine, '_create_copy_object', side_effect=failed_second):
            result = s.act('pilot:A', command); self.assertFalse(result.ok)
        self.assertEqual(2, len(calls)); self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        copies = [i for i in s.state.stack if i.kind == 'spell_copy']; self.assertEqual(2, len(copies))
        self.assertEqual([[ring.ref], [stone.ref]], [i.targets for i in copies])
        self.assertIsNone(self.resolve(s)); tokens = self.tokens(s)
        self.assertEqual(3, len(tokens))
        names = [s.engine._effective_card_data(c)['name'] for c in tokens]
        self.assertEqual(2, names.count('Sol Ring')); self.assertEqual(1, names.count('Mind Stone'))
        self.assertEqual(1, len(s.engine._current_turn_history('spell_cast')))
        self.assertEqual(2, controller_commander_cast_count(s.state, 'A')); self.finish(s, private)

    def test_actual_fury_keeps_its_spell_copy_result_and_all_copy_targets_are_independent(self):
        s = self.session(690070); fury = self.add(s.engine, 'Fury Storm', zone='hand')
        shock = self.add(s.engine, 'Shock', seat='B', zone='hand'); self.add(s.engine, 'Mountain', seat='B')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-fury-private')
        s.state.players['A'].commander_casts = {'prior': 1}
        self.prepare(s, mana={'C': 2, 'R': 2}); self.checkpoint(s); self.cast(s, 'B', shock, ['B'])
        original = s.state.stack[-1]; self.cast(s, 'A', fury, [original.ref])
        decision = self.resolve(s); self.assertEqual('semantic.storm', decision.kind)
        command = {'action_id': 'choose', 'copy_targets': [[original.ref]]}
        self.reject(s, 'D', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        decision = self.resolve(s); self.assertEqual('semantic.choice', decision.kind)
        self.reject(s, 'B', {'action_id': 'choose', 'targets': ['C']})
        s = self.replay(s, load=True); result = s.act('pilot:A', {'action_id': 'choose', 'targets': ['C']})
        self.assertTrue(result.ok, result.summary); decision = self.resolve(s)
        self.assertEqual('semantic.choice', decision.kind)
        result = s.act('pilot:A', {'action_id': 'choose', 'targets': ['D']}); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); self.assertEqual([40, 38, 38, 38], [s.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual(2, len(s.engine._current_turn_history('spell_cast')))
        self.assertEqual(1, controller_commander_cast_count(s.state, 'A')); self.finish(s, private)

    def test_actual_zero_commander_copy_count_creates_no_choice_and_retains_original_result(self):
        s = self.session(690080); card = self.add(s.engine, 'Empyrial Storm', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-zero-private')
        self.prepare(s, mana={'C': 4, 'W': 2}); self.checkpoint(s); self.cast(s, 'A', card)
        self.assertEqual(1, sum(i.semantic_key == COMMANDER_CAST_COPY_KEY for i in s.state.stack))
        self.assertIsNone(self.resolve(s)); self.assertEqual(1, len(self.tokens(s, subtype='angel')))
        self.assertEqual(0, controller_commander_cast_count(s.state, 'A')); self.finish(s, private)

    def test_actual_captain_locks_resolution_count_and_excludes_other_creatures(self):
        s = self.session(690030); card = self.add(s.engine, 'Captain Vargus Wrath'); other = self.add(s.engine, 'Grizzly Bears')
        self.add(s.engine, 'Fervor'); partner = self.add(s.engine, 'Surrak Dragonclaw', zone='command'); partner.is_commander = True
        s.state.players['A'].commander_casts = {'first': 1}
        for name in ('Island', 'Mountain', 'Forest', 'Wastes', 'Wastes'): self.add(s.engine, name, ref='captain-payer-' + str(len(s.state.cards)))
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-captain-private')
        self.prepare(s); self.checkpoint(s); self.attack(s, [(card, 'B')]); self.cast(s, 'A', partner)
        self.resolve_top(s); self.assertIsNone(self.resolve(s))
        self.assertEqual(3, s.engine._numeric_stat(card.object_id, 'power')); self.assertEqual(2, s.engine._numeric_stat(other.object_id, 'power'))
        self.finish(s, private)

    def test_actual_empyrial_counts_late_flash_commander_even_after_original_is_countered(self):
        s = self.session(690040); card = self.add(s.engine, 'Empyrial Storm', zone='hand')
        partner = self.add(s.engine, 'Surrak Dragonclaw', zone='command'); partner.is_commander = True
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B', ref='empyrial-blue-one'); self.add(s.engine, 'Island', seat='B', ref='empyrial-blue-two')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-empyrial-private')
        s.state.players['A'].commander_casts = {'earlier': 1}
        self.prepare(s, mana={'C': 6, 'W': 2, 'U': 1, 'R': 1, 'G': 1}); self.checkpoint(s)
        self.cast(s, 'A', card); self.cast(s, 'B', counter, [s.state.stack[0].ref]); self.resolve_top(s)
        self.cast(s, 'A', partner); self.resolve_top(s); s = self.replay(s, load=True)
        self.assertEqual(1, sum(i.semantic_key == COMMANDER_CAST_COPY_KEY for i in s.state.stack))
        self.resolve_top(s); self.assertEqual(2, len([i for i in s.state.stack if i.kind == 'spell_copy']))
        self.assertIsNone(self.resolve(s)); angels = self.tokens(s, subtype='angel'); self.assertEqual(2, len(angels))
        for angel in angels:
            self.assertEqual(4, s.engine._numeric_stat(angel.object_id, 'power')); self.assertIn('flying', s.engine._combat_keywords(angel))
        self.assertEqual(2, controller_commander_cast_count(s.state, 'A')); self.finish(s, private)

    def test_actual_hatut_permanent_copy_becomes_token_and_keeps_optional_entry_destroy(self):
        s = self.session(690050); card = self.add(s.engine, 'Hatut Zeraze Strike Force', zone='hand')
        victim = self.add(s.engine, 'Sol Ring', seat='B'); s.state.players['A'].commander_casts = {'prior': 1}
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-hatut-private')
        self.prepare(s, mana={'C': 3, 'W': 1}); self.checkpoint(s); self.cast(s, 'A', card)
        self.resolve_top(s); self.assertEqual(1, len([i for i in s.state.stack if i.kind == 'spell_copy']))
        self.resolve_top(s); self.target(s, 'A', [victim.ref]); decision = self.resolve(s)
        self.assertEqual('semantic.target', decision.kind)
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.target(s, 'A', []); self.assertIsNone(self.resolve(s))
        self.assertEqual(1, len(self.tokens(s, subtype='spy'))); self.assertEqual(1, controller_commander_cast_count(s.state, 'A'))
        self.finish(s, private)

    def test_actual_vanguard_keeps_its_optional_graveyard_return_and_live_spirit_bonus(self):
        s = self.session(690090); vanguard = self.add(s.engine, 'Vanguard of the Restless', zone='graveyard')
        spirit = self.add(s.engine, 'Lantern Spirit', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='commander-return-private')
        s.state.players['A'].commander_casts = {'prior': 1}
        self.prepare(s, mana={'C': 4, 'U': 1, 'W': 1}); self.checkpoint(s)
        self.cast_for(s, 'A', spirit, reserve={'C': 2, 'W': 1}); decision = self.resolve(s)
        self.assertEqual('semantic.choice', decision.kind)
        command = {'action_id': 'choose', 'pay': True}; self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[vanguard.object_id].zone)
        self.assertEqual(3, s.engine._numeric_stat(spirit.object_id, 'power'))
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[vanguard.object_id])); self.finish(s, private)

    def test_actual_resolution_count_mutant_is_killed_from_a_fresh_cast_checkpoint(self):
        # The positive Empyrial witness is run independently. A fresh game under
        # this mutant keeps the real ledger and changes only the copy reader.
        with patch('quorune.commander_cast_copies.controller_commander_cast_count', return_value=1):
            with self.assertRaises(AssertionError):
                self.test_actual_empyrial_counts_late_flash_commander_even_after_original_is_countered()
