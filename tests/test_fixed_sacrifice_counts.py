"""Printed sacrifice counts use existing APNAP choices and ordered results."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
from test_bound_effect_programs import record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.affected_player_sacrifice_templates import (
    FixedAffectedPlayerSacrificeTemplate, fixed_affected_player_sacrifice_effect_template,
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY as CAPABILITY,
)
from quorune.rules.affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
from scripts.build_test_database import build_fixture_database


class FixedSacrificeCountCompilerTests(unittest.TestCase):
    def test_fixed_counts_preserve_the_source_quantity_and_existing_context_owners(self):
        registry = load_default_capability_registry()
        for word, count in (('three', 3), ('four', 4), ('five', 5), ('six', 6), ('ten', 10), ('13', 13)):
            for subject, verb in (('You', 'sacrifice'), ('Target opponent', 'sacrifices'), ('Each player', 'sacrifices')):
                body = f'{subject} {verb} {word} lands'+(' of their choice.' if subject != 'You' else '.')
                template = fixed_affected_player_sacrifice_effect_template(body)
                self.assertIsNotNone(template, body)
                self.assertEqual(count, template.effects[0]['count'])
                for prefix, kind in (('', 'Sorcery'), ('When this creature enters, ', 'Creature — Human'),
                                     ('{2}: ', 'Creature — Human')):
                    ir = compile_oracle_card(record(prefix+body, type_line=kind), capability_registry=registry)
                    self.assertEqual('exact', ir.status, (body, ir.material_residuals))
                    self.assertIn(CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)

    def test_printed_thirteen_creatures_use_one_enabled_owner_order_and_fixed_price(self):
        from quorune.apnap_object_payment import ORDER_GRAVEYARD_ARRIVALS
        from quorune.compiler.fixed_public_alternative_costs import compile_fixed_public_alternative_cost
        from quorune.live_battlefield_quantity_model import LIVE_BATTLEFIELD_QUANTITY_CAPABILITY
        from quorune.public_alternative_costs import FixedPublicAlternativeCondition, FixedPublicAlternativeCostError
        from quorune.rules.graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY

        price = "You may pay {B} rather than pay this spell's mana cost if there are thirteen or more creatures on the battlefield."
        body = "Each player sacrifices thirteen creatures of their choice."
        registry = load_default_capability_registry()
        compiled = compile_oracle_card(record(price + "\n" + body, type_line="Sorcery"), capability_registry=registry)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        cast, resolve = compiled.faces[0].nodes
        self.assertEqual(("cast.cost", "resolve"), (cast.event, resolve.event))
        self.assertEqual((price, body), (cast.text, resolve.text))
        self.assertIn(LIVE_BATTLEFIELD_QUANTITY_CAPABILITY, cast.capability_dependencies)
        self.assertIn(GRAVEYARD_ARRIVAL_ORDER_CAPABILITY, resolve.capability_dependencies)
        effect, = resolve.effects
        self.assertEqual(13, effect["count"])
        self.assertIs(effect[ORDER_GRAVEYARD_ARRIVALS], True)
        for modified in ({**effect, ORDER_GRAVEYARD_ARRIVALS: 1}, {**effect, "count": 12},
                         {**effect, "players": "opponents"}):
            self.assertEqual((), fixed_affected_player_sacrifice_node_capabilities(
                effects=(modified,), target_schema=None, mechanic_ids=resolve.mechanics,
            ))
        cost = compile_fixed_public_alternative_cost(material_line=price, oracle_line=price, line_index=0)
        self.assertEqual(13, cost.condition.minimum_creature_count)
        for version, count in ((7, 0), (7, True), (7, "13"), (1, 13), (7.0, 13)):
            with self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCondition.from_dict({
                    **cost.condition.to_dict(), "schema_version": version, "minimum_creature_count": count,
                })

    def test_positive_integer_count_is_required_and_legacy_payloads_keep_their_shape(self):
        old = fixed_affected_player_sacrifice_effect_template('Each player sacrifices two creatures.')
        self.assertEqual(2, old.count)
        self.assertNotIn('schema_version', old.effects[0])
        template = fixed_affected_player_sacrifice_effect_template('Each player sacrifices six creatures.')
        for count in (True, False, 0, -1, '6', 6.0, None, {'x': 6}):
            with self.subTest(count=count), self.assertRaises(ValueError): replace(template, count=count)
            self.assertFalse(fixed_affected_player_sacrifice_node_capabilities(
                effects=({**template.effects[0], 'count': count},), target_schema=None, mechanic_ids=template.mechanics))
        for body in ('Each player sacrifices zero lands.', 'Each player sacrifices 0 lands.',
                     'Each player sacrifices X lands.', 'Each player sacrifices all lands.',
                     'Each player sacrifices three attacking creatures of their choice.',
                     'Each player may sacrifice three lands.', 'Each player sacrifices three lands unless they pay {3}.'):
            self.assertIsNone(fixed_affected_player_sacrifice_effect_template(body), body)


class FixedSacrificeCountNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        (folder/'basic.json').write_text(json.dumps(base), encoding='utf-8')
        path = folder/'fixed-counts.sqlite3'
        build_fixture_database([
            folder / "basic.json",
            ROOT / "tests/fixtures/fixed-sacrifice-count-cards.json",
            ROOT / "tests/fixtures/battlefield-creature-price-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed sacrifice counts', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def start(self, name, *, before=None):
        s = self.session(7011008+len(name))
        source = self.add(s.engine, name, zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='fixed-count-private-C')
        if before is not None: before(s)
        s.state.config.semantic_policy = 'trusted_only'
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 20, **{color: 10 for color in 'WUBRG'}})
        self.checkpoint(s)
        return s, source, private

    def permanents(self, s, counts, *, name='Grizzly Bears'):
        return {seat: [self.add(s.engine, name, seat=seat, ref=f'fixed-count-{seat}-{i}')
                       for i in range(count)] for seat, count in counts.items()}

    def choice(self, s, seat, cards, count):
        self.assertEqual('choice.apnap', s.state.pending_decision.kind)
        self.assertEqual(['pilot:'+seat], s.pending_principals())
        self.assertEqual(count, s.packet('pilot:'+seat, full=True)['decision']['ctx']['count'])
        refs = [card.ref for card in cards]
        command = {'action_id': 'choose', 'cards': refs}
        self.reject(s, 'D' if seat != 'D' else 'C', command)
        if count > 1:
            self.reject(s, seat, {**command, 'cards': refs[:-1]})
            self.reject(s, seat, {**command, 'cards': [refs[0]]*count})
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def token_scene(self, name, count, *, replaced=False, finish=True):
        bodies = {}
        replacement = {}
        def before(s):
            bodies.update(self.permanents(s, {'A': count+1, 'B': count-1, 'C': 0, 'D': 1}))
            if replaced: replacement['card'] = self.add(s.engine, 'Generic Fixed Count Destination Ward', seat='C')
            bodies['B'][0].owner = 'D'
        s, source, private = self.start(name, before=before)
        if replaced:
            self.assert_closed(s, replacement['card'])
        self.cast(s, 'A', source)
        self.assertIsNotNone(self.resolve(s))
        self.assertEqual(count, s.packet('pilot:A', full=True)['decision']['ctx']['count'])
        for seat, selected in (('A', bodies['A'][:count]), ('B', bodies['B']), ('D', bodies['D'])):
            if seat == 'D': s = self.replay(s, load=True)
            self.assertFalse(any(c.is_token and c.zone == 'battlefield' for c in s.state.cards.values()))
            self.choice(s, seat, selected, len(selected))
            if seat != 'D':
                self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield'
                                    for rows in bodies.values() for c in rows))
        self.assertIsNone(self.resolve(s))
        for rows in bodies.values():
            for card in rows:
                expected = 'battlefield' if card is bodies['A'][-1] else 'exile' if replaced else 'graveyard'
                self.assertEqual(expected, s.state.cards[card.object_id].zone)
                if replaced and expected == 'exile': self.assertEqual(1, s.state.cards[card.object_id].counters['void'])
        if not replaced: self.assertIn(bodies['B'][0].object_id, s.state.players['D'].zones['graveyard'])
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(count, len(tokens))
        for token in tokens:
            self.assertEqual('A', token.controller)
            self.assertEqual(name == 'Necrotic Hex', token.tapped)
            data = s.engine._effective_card_data(token)
            self.assertIn('Zombie' if name == 'Necrotic Hex' else 'Food', data['type_line'])
        if finish: self.finish(s, private)

    def test_actual_edict_counts_all_controllers_and_excludes_phased_out_creatures(self):
        for count in (12, 13):
            with self.subTest(count=count):
                def before(session):
                    self.permanents(session, {"B": 6, "C": 6, "D": count - 12})
                    phased = self.add(session.engine, "Grizzly Bears", seat="D", ref="edict-phased")
                    phased.phased_out = True

                session, source, private = self.start("Blasphemous Edict", before=before)
                packet = session.packet("pilot:A", full=True)
                cast = next(row for row in packet["decision"]["ctx"]["legal"]["actions"]
                            if row["id"] == f"cast:{source.ref}")
                alternates = [row for row in cast["cost_options"] if row["kind"] == "alternate"]
                self.assertEqual(int(count == 13), len(alternates))
                self.assertNotIn(private.ref, json.dumps(packet))
                command = {"action_id": cast["id"], "cost_option": "plain-alternative-ab1", "pay": "auto"}
                if count == 12:
                    self.reject(session, "A", command)
                    self.assertTrue(session.act("pilot:A", {"action_id": cast["id"], "pay": "auto"}).ok)
                else:
                    self.assertEqual({"B": 1}, {key: value for key, value in alternates[0]["requirements"].items() if value})
                    self.assertTrue(session.act("pilot:A", command).ok)
                self.replay(session, load=True)

    def test_actual_edict_preserves_public_apnap_choices_and_separate_physical_owner_orders(self):
        bodies = {}

        def before(session):
            bodies.update(self.permanents(session, {"A": 14, "B": 3, "D": 2}))
            bodies["A"][0].owner = "B"
            bodies["B"][0].owner = "A"
            bodies["D"][1].is_token = True
            bodies["D"][1].object_kind = "token"

        session, source, private = self.start("Blasphemous Edict", before=before)
        cast = {"action_id": f"cast:{source.ref}", "cost_option": "plain-alternative-ab1", "pay": "auto"}
        result = session.act("pilot:A", cast)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNotNone(self.resolve(session))
        selected = {"A": bodies["A"][:13], "B": bodies["B"], "D": bodies["D"]}
        earlier = {}
        for seat, cards in selected.items():
            decision = session.packet("pilot:" + seat, full=True)["decision"]
            public = decision["ctx"]["prior_public_choices"]
            self.assertEqual(earlier, {key: refs for key, refs in public.items() if refs})
            self.choice(session, seat, cards, len(cards))
            earlier[seat] = [card.ref for card in cards]
            self.assertTrue(all(session.state.cards[card.object_id].zone == "battlefield"
                                for rows in bodies.values() for card in rows))

        expected = {
            owner: [card for cards in selected.values() for card in cards
                    if card.owner == owner and not card.is_token]
            for owner in ("A", "B", "D")
        }
        session = self.replay(session, load=True)
        for owner in ("A", "B"):
            self.assertEqual("permanent.composition_order", session.state.pending_decision.kind)
            self.assertEqual(["pilot:" + owner], session.pending_principals())
            order = list(reversed(expected[owner]))
            decision = session.packet("pilot:" + owner, full=True)["decision"]
            self.assertEqual({card.ref for card in order}, {row["id"] for row in decision["ctx"]["cards"]})
            self.assertNotIn(private.ref, json.dumps(decision))
            self.reject(session, "D", {"action_id": "order", "cards": [card.ref for card in order]})
            self.reject(session, owner, {"action_id": "order", "cards": [card.ref for card in order[:-1]]})
            command = {"action_id": "order", "cards": [card.ref for card in order]}
            if owner == "B":
                from quorune import composition_departure_commit

                commit = composition_departure_commit.commit_prepared_composition_departures

                def fail_after_commit(*args, **kwargs):
                    commit(*args, **kwargs)
                    raise RuntimeError("injected final ordered sacrifice failure")

                before = authoritative_state_hash(session.state)
                with patch.object(composition_departure_commit, "commit_prepared_composition_departures", fail_after_commit):
                    with self.assertRaisesRegex(RuntimeError, "injected final ordered sacrifice failure"):
                        session.act("pilot:" + owner, command)
                self.assertEqual(before, authoritative_state_hash(session.state))
            result = session.act("pilot:" + owner, command)
            self.assertTrue(result.ok, result.summary)
            if owner == "A":
                self.assertTrue(all(session.state.cards[card.object_id].zone == "battlefield"
                                    for rows in bodies.values() for card in rows))
        self.assertIsNone(self.resolve(session))
        for owner, cards in expected.items():
            ids = {card.object_id for card in cards}
            actual = [oid for oid in session.state.players[owner].zones["graveyard"] if oid in ids]
            self.assertEqual([card.object_id for card in reversed(cards)], actual)
        self.assertEqual("battlefield", session.state.cards[bodies["A"][-1].object_id].zone)
        self.replay(session, load=True)

    def test_actual_hex_and_taste_collect_full_counts_before_sacrificing_then_create_only_later_tokens(self):
        for name, count in (('Taste of Death', 3), ('Necrotic Hex', 6)):
            with self.subTest(name=name): self.token_scene(name, count)

    def test_actual_six_creature_sacrifices_use_the_canonical_destination_owner_before_token_creation(self):
        self.token_scene('Necrotic Hex', 6, replaced=True)

    def test_actual_wildfire_and_destructive_force_sacrifice_lands_before_damaging_remaining_creatures(self):
        for name, count in (('Wildfire', 4), ('Destructive Force', 5)):
            with self.subTest(name=name):
                lands = {}
                creatures = {}
                def before(s):
                    lands.update(self.permanents(s, {'A': count, 'B': count-1, 'C': 1, 'D': 0}, name='Forest'))
                    lands['A'].insert(0, self.add(s.engine, 'Dryad Arbor', ref='fixed-count-creature-land'))
                    creatures['wall'] = self.add(s.engine, 'Wall of Stone', seat='D')
                    creatures['bear'] = self.add(s.engine, 'Grizzly Bears', seat='D', ref='fixed-count-damage-control')
                s, source, private = self.start(name, before=before)
                self.cast(s, 'A', source)
                self.assertIsNotNone(self.resolve(s))
                for seat in 'ABC':
                    selected = lands[seat][:count]
                    self.choice(s, seat, selected, len(selected))
                self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard', s.state.cards[lands['A'][0].object_id].zone)
                self.assertEqual('graveyard', s.state.cards[creatures['bear'].object_id].zone)
                self.assertEqual('battlefield', s.state.cards[creatures['wall'].object_id].zone)
                self.assertEqual(count, s.state.cards[creatures['wall'].object_id].marked_damage)
                self.finish(s, private)

    def test_actual_monstrous_designation_requires_three_land_choices_and_cannot_trigger_again(self):
        lands = {}
        def before(s): lands.update(self.permanents(s, {'A': 4, 'B': 2, 'C': 0, 'D': 0}, name='Forest'))
        s, source, private = self.start('Ember Swallower', before=before)
        self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s))
        self.priority(s, 'A')
        row = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                   if row['id'].startswith('activate:'+source.ref+':'))
        command = {'action_id': row['id'], 'pay': 'auto'}
        self.reject(s, 'D', command)
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNotNone(self.resolve(s))
        self.assertEqual(3, s.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(3, s.state.cards[source.object_id].monstrous_value)
        self.choice(s, 'A', lands['A'][:3], 3)
        self.choice(s, 'B', lands['B'], 2)
        self.assertIsNone(self.resolve(s))
        self.priority(s, 'A')
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(3, s.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual('battlefield', s.state.cards[lands['A'][-1].object_id].zone)
        self.finish(s, private)

    def test_actual_gambit_sequences_private_discard_life_loss_and_three_bat_sacrifices_after_departure(self):
        destroy = {}
        def before(s):
            destroy['spell'] = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
            s.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
        s, source, private = self.start("Greed's Gambit", before=before)
        initial_life = s.state.players['A'].life
        self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s))
        bats = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(3, len(bats))
        self.assertEqual(initial_life+6, s.state.players['A'].life)
        self.cast(s, 'B', destroy['spell'], [source.ref])
        self.assertIsNotNone(self.resolve(s))
        hand = [s.state.cards[oid] for oid in s.state.players['A'].zones['hand'][:3]]
        for seat in 'BCD':
            packet = json.dumps(packet_without_decision_capability(s.packet('pilot:'+seat, full=True)))
            for card in hand: self.assertNotIn(card.ref, packet)
        self.choice(s, 'A', hand, 3)
        self.assertEqual(initial_life, s.state.players['A'].life)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in bats))
        s = self.replay(s, load=True)
        self.choice(s, 'A', bats, 3)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone != 'battlefield' for c in bats))
        self.finish(s, private)

    def test_actual_transformation_keeps_the_opponent_target_and_their_three_sacrifice_choices(self):
        bodies = {}
        def before(s): bodies.update(self.permanents(s, {'A': 3, 'B': 4, 'C': 0, 'D': 0}))
        s, source, private = self.start('Voldaren Pariah // Abolisher of Bloodlines', before=before)
        self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s))
        self.priority(s, 'A')
        row = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                   if row['id'].startswith('activate:'+source.ref+':'))
        command = {'action_id': row['id'], 'pay': 'auto', 'cost_cards': [c.ref for c in bodies['A']]}
        self.reject(s, 'D', command)
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in bodies['A']))
        self.assertIsNotNone(self.resolve(s))
        self.target(s, 'A', ['B'])
        self.assertIsNotNone(self.resolve(s))
        self.choice(s, 'B', bodies['B'][:3], 3)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[bodies['B'][-1].object_id].zone)
        self.finish(s, private)

    def test_actual_count_reduction_mutant_is_killed_by_the_required_third_choice(self):
        original = FixedAffectedPlayerSacrificeTemplate.effects.fget
        def reduced(template):
            return tuple({**effect, 'count': 2} if template.count > 2 else effect for effect in original(template))
        with patch.object(FixedAffectedPlayerSacrificeTemplate, 'effects', property(reduced)), self.assertRaisesRegex(AssertionError, '3 != 2'):
            self.token_scene('Taste of Death', 3, finish=False)
