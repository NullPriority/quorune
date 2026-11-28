from __future__ import annotations

"""CR 702.172/601.2: selected Spree mode costs join the total casting cost."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, capability_dependencies_for_node, load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


OWNER = 'casting.additional_cost.spree_fixed_modes'
TEXT = 'Spree\n+ {1} \u2014 Draw a card.\n+ {2}{B} \u2014 You gain 3 life.'


class SpreeModeCostCompilerTests(unittest.TestCase):
    def compile(self, text=TEXT):
        return compile_oracle_card(replace(record(text, 'Sorcery'), keywords=('Spree',), mana_cost='{W}', mana_value=1),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')

    def test_fixed_mode_costs_close_with_original_spans_and_independent_modal_bodies(self):
        for text in (TEXT, TEXT.replace('{2}{B}', '{0}'), TEXT.replace('Draw a card.', 'Destroy target artifact.')):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertIn(OWNER, node.capability_dependencies)
                self.assertEqual(['mode_1', 'mode_2'], list(node.target_schema['modes']))
                self.assertEqual(1, node.target_schema['min_modes'])
                self.assertEqual(2, node.target_schema['max_modes'])
                self.assertEqual(1, node.cost['spree']['schema_version'])
                self.assertEqual(mana_cost_to_vector('{1}')[0], node.cost['spree']['mode_costs']['mode_1'])

    def test_mixed_copy_and_single_retarget_remain_independent_spree_branches(self):
        from quorune.stack_spell_copy_model import COPYABLE_STACK_ITEM_PREDICATE
        from quorune.stack_target_change_model import SINGLE_ANNOUNCED_STACK_TARGET

        text = (
            "Spree\n+ {1} — Copy target instant spell, sorcery spell, activated ability, or triggered ability. "
            "You may choose new targets for the copy.\n+ {1} — Change the target of target spell or ability with a single target."
        )
        compiled = self.compile(text)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = compiled.faces[0].nodes
        self.assertEqual(text, node.text)
        copy, change = node.target_schema["modes"].values()
        self.assertEqual(COPYABLE_STACK_ITEM_PREDICATE, copy["groups"][0]["predicate"])
        self.assertEqual(SINGLE_ANNOUNCED_STACK_TARGET, change["groups"][0]["predicate"])
        self.assertIn("stack.copy.fixed_qualified_target", node.capability_dependencies)
        self.assertNotEqual("exact", self.compile(text.replace("sorcery spell", "creature spell")).status)

    def test_selected_costs_targets_and_free_base_options_remain_correlated(self):
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        from quorune.rules.casting.spree_costs import with_spree_mode_cost_options
        node = ir.faces[0].nodes[0]
        program = SimpleNamespace(cost_schema=node.cost, target_schema=node.target_schema, coverage=node.mechanics)
        for base_cost, expected in (('{W}', '{3}{W}{B}'), ('{0}', '{3}{B}')):
            base = {'id': 'normal', 'kind': 'normal', 'requirements': mana_cost_to_vector(base_cost)[0]}
            options = with_spree_mode_cost_options(program, node.cost, [base], {'modes': ['mode_2', 'mode_1']}, hint=False)
            self.assertEqual(1, len(options))
            self.assertEqual(mana_cost_to_vector(expected)[0], options[0]['requirements'])
            self.assertEqual(['mode_1', 'mode_2'], options[0]['spree_modes'])
            self.assertEqual(2, options[0]['target_schema']['min_modes'])
            self.assertEqual(['mode_1', 'mode_2'], list(options[0]['target_schema']['modes']))
            self.assertEqual(mana_cost_to_vector(base_cost)[0], base['requirements'])
        base = {'id': 'normal', 'kind': 'normal', 'requirements': mana_cost_to_vector('{W}')[0]}
        self.assertEqual(3, len(with_spree_mode_cost_options(program, node.cost, [base], {}, hint=True)))
        for modes in ([], ['mode_1', 'mode_1'], ['unknown']):
            self.assertEqual([], with_spree_mode_cost_options(program, node.cost, [base], {'modes': modes}, hint=False))
        single, = with_spree_mode_cost_options(program, node.cost, [base], {'modes': ['mode_2']}, hint=False)
        self.assertEqual(['mode_2'], list(single['target_schema']['modes']))

    def test_unknown_prices_bodies_and_malformed_mode_cost_ownership_fail_closed(self):
        for text in (TEXT.replace('{1}', '{X}'), TEXT.replace('{1}', '{W/U}'),
                     TEXT.replace('Draw a card.', 'Rewrite history.'), TEXT + '\nDraw a card.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        for change in ({'mode_1': {'GENERIC': True}}, {'unknown': mana_cost_to_vector('{1}')[0]}):
            cost = deepcopy(node.cost)
            cost['spree']['mode_costs'].update(change)
            self.assertFalse(capability_dependencies_for_node(effects=node.effects, target_schema=node.target_schema,
                mechanic_ids=node.mechanics, cost_schema=cost))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (OWNER, 'choice.modal.fixed_nonrepeating'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['Spree owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(replace(record(TEXT, 'Sorcery'), keywords=('Spree',)),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)


class SpreeModeCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    from inherited_composition_fixture import InheritedNativeFixture as _combat_actions
    until = _combat_actions.until
    priority = _combat_actions.priority


    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'spree-mode-costs.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/spree-mode-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Spree witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def reject(self, session, action, actor='A', **response):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + actor, {'action_id': action, **response}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def surprise_act(self, session, seat, action, **choices):
        result = session.act("pilot:" + seat, {"action_id": action, **choices})
        self.assertTrue(result.ok, result.summary)

    def surprise_mill_cards(self, session, cards, *, short=False):
        if short:
            for object_id in tuple(session.state.players["A"].zones["library"]):
                session.engine.move_card(object_id, "hand", reason="Set a short library", log=False)
        result = []
        for name, ref in cards:
            card = self.add(session.engine, name, zone="library", ref=ref)
            card.known_to = []
            card.revealed_to = []
            result.append(card)
        return result

    def test_smugglers_surprise_all_modes_in_printed_order_with_private_entries_and_protection(self):
        session = self.session(416050)
        source = self.add(session.engine, "Smuggler's Surprise", zone="hand")
        renata = self.add(session.engine, "Renata, Called to the Hunt", zone="hand")
        small = self.add(session.engine, "Generic Bound Body", ref="surprise-small")
        foreign = self.add(session.engine, "Rumbling Baloth", seat="B", ref="surprise-foreign-large")
        murder = self.add(session.engine, "Murder", seat="B", zone="hand")
        outside = self.add(session.engine, "Forest", zone="graveyard", ref="not-from-this-mill")
        private = self.add(session.engine, "Generic Bound Growth", zone="hand", ref="unchosen-private-spell")
        large, land, other, bolt = self.surprise_mill_cards(session, (
            ("Rumbling Baloth", "surprise-milled-large"), ("Forest", "surprise-milled-land"),
            ("Grizzly Bears", "surprise-milled-other"), ("Lightning Bolt", "surprise-milled-instant"),
        ))
        for card in (source, renata, murder):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 7, "G": 2})
        session.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(session)
        action = "cast:" + source.ref
        offer = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"] == action)
        self.assertEqual(7, len(offer["cost_options"]))
        mode3 = next(row["id"] for row in offer["cost_options"] if row["target_schema"]["legal_modes"] == ["mode_3"])
        for modes in ([], ["mode_1", "mode_1"], ["mode_4"]):
            self.reject(session, action, modes=modes, pay="auto")
        self.reject(session, action, modes=["mode_1", "mode_2", "mode_3"], cost_option=mode3, pay="auto")
        self.reject(session, action, modes=["mode_1", "mode_2", "mode_3"], pay="manual", payment={"G": 1})
        expected = ("mode_1", "mode_2", "mode_3")
        self.surprise_act(session, "A", action, modes=list(reversed(expected)), pay="auto")
        self.assertEqual(expected, tuple(session.state.stack[-1].modes))
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        packet = session.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({large.ref, land.ref, other.ref}, {row["id"] for row in packet["objects"]})
        self.reject(session, "choose", "B", cards=[large.ref])
        for invalid in ([bolt.ref], [outside.ref], [large.ref, large.ref], [large.ref, land.ref, other.ref]):
            self.reject(session, "choose", cards=invalid)
        session = self.replay(session, load=True)
        self.surprise_act(session, "A", "choose", cards=[large.ref, land.ref])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual("hand", session.state.cards[large.object_id].zone)
        packet = session.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(large.ref, {row["id"] for row in packet["objects"]})
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:B", full=True)))
        self.reject(session, "choose", cards=[land.ref])
        self.reject(session, "pass", "B")
        session = self.replay(session, load=True)
        self.surprise_act(session, "A", "choose", cards=[renata.ref, large.ref])
        self.assertIsNone(self.resolve(session))
        for card in (renata, large):
            self.assertEqual("battlefield", session.state.cards[card.object_id].zone)
        large = session.state.cards[large.object_id]
        self.assertEqual(4, session.engine._numeric_stat(large.object_id, "power"))
        self.assertEqual(0, large.counters.get("+1/+1", 0))
        for target, qualifies in ((large, True), (small, False), (foreign, False)):
            current = session.state.cards[target.object_id]
            keywords = session.engine._combat_keywords(current)
            self.assertEqual(qualifies, "hexproof" in keywords)
            self.assertEqual(qualifies, "indestructible" in keywords)
        for _ in range(4):
            if session.state.priority_player == "B":
                break
            seat = session.pending_principals()[0].removeprefix("pilot:")
            self.surprise_act(session, seat, "pass")
        self.assertEqual("B", session.state.priority_player)
        murder_offer = next(row for row in session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                            if row["id"] == "cast:" + murder.ref)
        self.assertIn(small.ref, murder_offer["target_schema"]["legal_refs"])
        self.assertNotIn(large.ref, murder_offer["target_schema"]["legal_refs"])
        self.reject(session, murder_offer["id"], "B", targets=[large.ref], pay="auto")
        self.replay(session)

    def test_smugglers_surprise_optional_short_mill_uses_committed_public_exile(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                session = self.session(416060 + replacement)
                source = self.add(session.engine, "Smuggler's Surprise", zone="hand")
                outside = self.add(session.engine, "Forest", zone="graveyard", ref="surprise-other-land")
                creature, land, instant = self.surprise_mill_cards(session, (
                    ("Grizzly Bears", "surprise-short-creature"), ("Forest", "surprise-short-land"),
                    ("Lightning Bolt", "surprise-short-instant"),
                ), short=True)
                if replacement:
                    for name, seat in (("Rest in Peace", "C"), ("Leyline of the Void", "D")):
                        self.assert_closed(session, self.add(session.engine, name, seat=seat))
                self.prepare(session, mana={"C": 2, "G": 1})
                self.checkpoint(session)
                self.surprise_act(session, "A", "cast:" + source.ref, modes=["mode_1"], pay="auto")
                pending = self.resolve(session)
                if replacement:
                    self.assertEqual("replacement.order", pending.kind)
                    session = self.replay(session, load=True)
                    for _ in range(5):
                        if pending.kind != "replacement.order":
                            break
                        opts = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                        self.surprise_act(session, "A", "choose", replacement=opts[0]["id"])
                        pending = self.resolve(session)
                self.assertEqual("semantic.choice", pending.kind)
                snapshot = session.packet("pilot:A", full=True)["decision"]["ctx"]
                self.assertEqual({creature.ref, land.ref}, {row["id"] for row in snapshot["objects"]})
                self.reject(session, "choose", cards=[outside.ref])
                self.reject(session, "choose", cards=[instant.ref])
                self.surprise_act(session, "A", "choose", cards=[creature.ref] if replacement else [])
                pending = self.resolve(session)
                if replacement:
                    self.assertEqual("replacement.order", pending.kind)
                    self.assertEqual("hand", session.state.cards[creature.object_id].zone)
                    self.assertEqual("stack", session.state.cards[source.object_id].zone)
                    session = self.replay(session, load=True)
                    options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                    self.surprise_act(session, "A", "choose", replacement=options[0]["id"])
                    pending = self.resolve(session)
                self.assertIsNone(pending)
                self.assertEqual("hand" if replacement else "graveyard", session.state.cards[creature.object_id].zone)
                expected = "exile" if replacement else "graveyard"
                self.assertEqual(expected, session.state.cards[land.object_id].zone)
                self.assertEqual(expected, session.state.cards[instant.object_id].zone)
                self.assertEqual("graveyard", session.state.cards[outside.object_id].zone)
                mill, = (event for event in session.state.events if event.code == "card.mill")
                self.assertEqual(3, mill.details["count"])
                self.replay(session)

    def test_smugglers_surprise_unselected_modes_stay_unexecuted_on_private_decline(self):
        session = self.session(416063)
        source = self.add(session.engine, "Smuggler's Surprise", zone="hand")
        candidate = self.add(session.engine, "Rumbling Baloth", zone="hand", ref="declined-private-creature")
        fixed = self.add(session.engine, "Rumbling Baloth", ref="unprotected-large-creature")
        self.prepare(session, mana={"C": 4, "G": 2})
        self.checkpoint(session)
        self.surprise_act(session, "A", "cast:" + source.ref, modes=["mode_2"], pay="auto")
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.surprise_act(session, "A", "choose", cards=[])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[candidate.object_id].zone)
        self.assertFalse(set(session.engine._combat_keywords(fixed)).intersection({"hexproof", "indestructible"}))
        self.assertFalse(any(event.code == "card.mill" for event in session.state.events))
        self.replay(session)

    def test_smugglers_surprise_committed_mill_shape_is_closed(self):
        from unittest.mock import patch
        from quorune.rules.milled_creature_land_to_hand import MillCreatureLandToHand, mill_hand_node_capabilities

        source = self.db.lookup("Smuggler's Surprise", fuzzy=False)
        ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(1, node.target_schema["min_modes"])
        self.assertEqual(3, node.target_schema["max_modes"])
        self.assertEqual(["mode_1", "mode_2", "mode_3"], list(node.target_schema["modes"]))
        with patch("quorune.compiler.mill_templates.committed_mill_card_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(source, capability_registry=self.registry).status)
        effect = MillCreatureLandToHand().effect()
        for change in ({"count": 3}, {"player": "$target.0"},
                       {"followup": {"schema_version": True, "kind": effect["followup"]["kind"]}}):
            self.assertFalse(mill_hand_node_capabilities(({**effect, **change},), None))

    def test_actual_raid_selected_modes_charge_sum_and_bind_only_selected_targets(self):
        session = self.session(416000)
        source = self.add(session.engine, 'Requisition Raid', zone='hand', ref='spree-original-raid')
        artifact = self.add(session.engine, 'Generic Spree Artifact', seat='B', ref='spree-artifact-target')
        enchantment = self.add(session.engine, 'Generic Spree Enchantment', seat='C', ref='spree-enchantment-target')
        untouched = self.add(session.engine, 'Generic Bound Body', seat='D', ref='spree-unselected-recipient')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='spree-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'W': 1})
        self.checkpoint(session)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                       if row['id'] == 'cast:' + source.ref)
        targets = [artifact.ref, enchantment.ref]
        for modes, selected in (([], []), (['mode_1', 'mode_1'], targets), (['mode_1'], targets), (['unknown'], [])):
            self.reject(session, offered['id'], modes=modes, targets=selected, pay='auto')
        self.reject(session, offered['id'], 'D', modes=['mode_1', 'mode_2'], targets=targets, pay='auto')
        cheap = next(row['id'] for row in offered['cost_options'] if row['target_schema']['legal_modes'] == ['mode_1'])
        self.reject(session, offered['id'], modes=['mode_1', 'mode_2'], targets=targets, cost_option=cheap, pay='auto')
        self.reject(session, offered['id'], modes=['mode_1', 'mode_2'], targets=targets, pay='manual', payment={'W': 1})
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'modes': ['mode_2', 'mode_1'],
            'targets': targets, 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(['mode_1', 'mode_2'], session.state.stack[-1].modes)
        self.assertEqual('{W}', session.engine._effective_card_data(session.state.cards[source.object_id])['mana_cost'])
        session = self.replay(session, load=True)
        self.resolve(session)
        for card in (artifact, enchantment, source):
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        self.assertEqual({}, session.state.cards[untouched.object_id].counters)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_electromancer_reduces_the_total_after_spree_additional_costs(self):
        session = self.session(416010)
        source = self.add(session.engine, 'Requisition Raid', zone='hand', ref='spree-reduced-raid')
        reducer = self.add(session.engine, 'Goblin Electromancer', ref='spree-actual-reducer')
        artifact = self.add(session.engine, 'Generic Spree Artifact', seat='B', ref='spree-reduced-artifact')
        enchantment = self.add(session.engine, 'Generic Spree Enchantment', seat='C', ref='spree-reduced-enchantment')
        for card in (source, reducer):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                       if row['id'] == 'cast:' + source.ref)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'modes': ['mode_1', 'mode_2'],
            'targets': [artifact.ref, enchantment.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.resolve(session)
        for card in (artifact, enchantment):
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        self.replay(session, load=True)

    def _heist_offer(self, game, source):
        from inherited_composition_fixture import InheritedNativeFixture as H

        H.priority(self, game, "A")
        return next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"] == "cast:" + source.ref)

    def _heist_treasures(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Treasure Token"]

    def test_actual_heist_spree_own_combat_counts_each_creature_and_only_the_chosen_opponent(self):
        from inherited_composition_fixture import InheritedNativeFixture as H

        game = self.session(416111)
        source = self.add(game.engine, "Great Train Heist", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="heist-attacker-1")
        second = self.add(game.engine, "Grizzly Bears", ref="heist-attacker-2")
        for card in (first, second):
            card.acquired_control_turn_count = -1
        for index in range(7):
            self.add(game.engine, "Mountain", ref=f"heist-mountain-{index}")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        H.attack(self, game, [(first, "B"), (second, "C")])
        self.assertTrue(game.state.cards[first.object_id].tapped)
        offer = self._heist_offer(game, source)
        modes = ["mode_1", "mode_2", "mode_3"]
        all_modes, = [option for option in offer["cost_options"]
                      if option["target_schema"]["legal_modes"] == modes]
        self.assertEqual((4, 3), (all_modes["requirements"]["GENERIC"], all_modes["requirements"]["R"]))
        one_mode, = [option for option in offer["cost_options"]
                     if option["target_schema"]["legal_modes"] == ["mode_1"]]
        self.reject(game, offer["id"], modes=modes, targets=["A"], pay="auto")
        self.reject(game, offer["id"], modes=modes, targets=["B"], pay="auto", cost_option=one_mode["id"])
        self.reject(game, offer["id"], "D", modes=modes, targets=["B"], pay="auto")
        result = game.act("pilot:A", {"action_id": offer["id"], "modes": list(reversed(modes)),
                                      "targets": ["B"], "cost_option": all_modes["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(modes, game.state.stack[-1].modes)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertFalse(game.state.cards[first.object_id].tapped)
        for card in (first, second):
            self.assertEqual(3, game.engine._numeric_stat(card.object_id, "power"))
            self.assertIn("first strike", game.engine._combat_keywords(game.state.cards[card.object_id]))
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        initial = self._heist_treasures(game)
        self.assertEqual(1, len(initial))
        self.assertTrue(initial[0].tapped)
        self.assertEqual((37, 37), tuple(game.state.players[seat].life for seat in "BC"))
        H.attack(self, game, [(first, "B"), (second, "B")])
        self.until(game, lambda current: current.state.step == "end_combat" and not current.state.stack
                   and current.state.pending_decision.kind == "priority")
        tokens = self._heist_treasures(game)
        self.assertEqual(3, len(tokens))
        self.assertTrue(all(card.tapped and card.controller == "A" for card in tokens))
        self.assertEqual((31, 37), tuple(game.state.players[seat].life for seat in "BC"))
        self.replay(game, load=True)

    def test_actual_heist_always_untaps_and_schedules_no_extra_combat_outside_your_combat(self):
        from inherited_composition_fixture import InheritedNativeFixture as H

        for enemy_combat in (False, True):
            with self.subTest(enemy_combat=enemy_combat):
                game = self.session(416115 + int(enemy_combat))
                source = self.add(game.engine, "Great Train Heist", zone="hand")
                own = self.add(game.engine, "Grizzly Bears", ref="heist-own-tapped")
                enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="heist-enemy-attacker")
                own.tapped = True
                enemy.acquired_control_turn_count = -1
                for index in range(4):
                    self.add(game.engine, "Mountain", ref=f"heist-noncombat-mountain-{index}")
                self.assert_closed(game, source)
                self.prepare(game, active="B" if enemy_combat else "A")
                self.checkpoint(game)
                if enemy_combat:
                    self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
                    result = game.act("pilot:B", {"action_id": "attack", "attackers": {enemy.ref: "D"}})
                    self.assertTrue(result.ok, result.summary)
                    H.priority(self, game, "A")
                    self.assertEqual(("combat", "B"), (game.state.phase, game.state.active_player))
                offer = self._heist_offer(game, source)
                self.reject(game, offer["id"], modes=["mode_1"], targets=["B"], pay="auto")
                result = game.act("pilot:A", {"action_id": offer["id"], "modes": ["mode_1"], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.cards[own.object_id].tapped)
                self.assertEqual(0, game.state.extra_combat_phases)
                self.assertFalse(game.state.delayed_triggers)
                self.replay(game, load=True)

    def test_actual_heist_uses_post_replacement_damage_and_a_later_creature_incarnation(self):
        for prevented in (False, True):
            with self.subTest(prevented=prevented):
                game = self.session(416119 + int(prevented))
                source = self.add(game.engine, "Great Train Heist", zone="hand")
                attacker = self.add(game.engine, "Rakdos Shred-Freak")
                response = self.add(
                    game.engine, "Fog" if prevented else "Cloudshift",
                    seat="B" if prevented else "A", zone="hand",
                )
                if prevented:
                    self.add(game.engine, "Forest", seat="B")
                self.assert_closed(game, source)
                self.assert_closed(game, response)
                self.prepare(game, mana={"C": 2, "R": 2, "W": 1})
                self.checkpoint(game)
                offer = self._heist_offer(game, source)
                result = game.act("pilot:A", {"action_id": offer["id"], "modes": ["mode_2", "mode_3"],
                                              "targets": ["B"], "pay": "manual", "payment": {"C": 2, "R": 2}})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(3, game.engine._numeric_stat(attacker.object_id, "power"))
                if not prevented:
                    incarnation = attacker.logical_object_id
                    blink = game.act("pilot:A", {"action_id": "cast:" + response.ref,
                                                "targets": [attacker.ref], "pay": "auto"})
                    self.assertTrue(blink.ok, blink.summary)
                    self.assertIsNone(self.resolve(game))
                    attacker = game.state.cards[attacker.object_id]
                    self.assertNotEqual(incarnation, attacker.logical_object_id)
                    self.assertEqual(2, game.engine._numeric_stat(attacker.object_id, "power"))
                    self.assertNotIn("first strike", game.engine._combat_keywords(attacker))
                self._combat_actions.attack(self, game, [(attacker, "B")])
                if prevented:
                    self.priority(game, "B")
                    fog = game.act("pilot:B", {"action_id": "cast:" + response.ref, "pay": "auto"})
                    self.assertTrue(fog.ok, fog.summary)
                    self.assertIsNone(self.resolve(game))
                self.until(game, lambda current: current.state.step == "end_combat" and not current.state.stack
                           and current.state.pending_decision.kind == "priority")
                self.assertEqual(40 if prevented else 38, game.state.players["B"].life)
                self.assertEqual(0 if prevented else 1, len(self._heist_treasures(game)))
                self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
                self.assertFalse(game.state.delayed_triggers[0].active)
                self.replay(game, load=True)


RESTORATION_TEXT = (
    "Tiered (Choose one additional cost.)\n"
    "• Cure — {0} — Target permanent gains hexproof and indestructible until end of turn.\n"
    "• Cura — {1} — Target permanent gains hexproof and indestructible until end of turn. You gain 3 life.\n"
    "• Curaga — {3}{W} — Permanents you control gain hexproof and indestructible until end of turn. You gain 6 life."
)


class TieredModeCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = SpreeModeCostRuntimeTests.session
    add = SpreeModeCostRuntimeTests.add
    checkpoint = SpreeModeCostRuntimeTests.checkpoint
    resolve = SpreeModeCostRuntimeTests.resolve
    replay = SpreeModeCostRuntimeTests.replay
    prepare = SpreeModeCostRuntimeTests.prepare
    assert_closed = SpreeModeCostRuntimeTests.assert_closed
    reject = SpreeModeCostRuntimeTests.reject
    tearDownClass = classmethod(SpreeModeCostRuntimeTests.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / "tiered-mode-costs.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "spree-mode-cost-cards.json",
            "global-alternative-cost-cards.json", "fixed-main-phase-cast-results-cards.json",
            "temporary-target-interaction-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Single priced mode", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def action(self, game, card, seat="A"):
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        return next(row for row in context["legal"]["actions"] if row["id"] == f"cast:{card.ref}")

    def cast(self, game, card, seat="A", **response):
        result = game.act("pilot:" + seat, {"action_id": f"cast:{card.ref}", **response})
        self.assertTrue(result.ok, result.summary)

    def priority(self, game, seat):
        for _ in range(12):
            decision = game.state.pending_decision
            self.assertIsNotNone(decision)
            self.assertEqual("priority", decision.kind)
            if decision.actors == [seat]:
                return
            result = game.act(game.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Requested player did not receive priority")

    def protects(self, game, card):
        keywords = game.engine._combat_keywords(game.state.cards[card.object_id])
        return {"hexproof", "indestructible"}.issubset(keywords)

    def test_restoration_magic_offers_and_pays_exactly_one_mode(self):
        payments = (
            ("mode_1", {"W": 1}, "Cure", 0),
            ("mode_2", {"W": 1, "C": 1}, "Cura", 3),
            ("mode_3", {"W": 2, "C": 3}, "Curaga", 6),
        )
        for i, (mode, payment, label, life) in enumerate(payments):
            with self.subTest(mode=mode):
                game = self.session(70218301 + i)
                source = self.add(game.engine, "Restoration Magic", zone="hand", ref="tiered-magic")
                ally = self.add(game.engine, "Generic Bound Body", ref="tiered-ally")
                land = self.add(game.engine, "Generic Bound Plains", ref="tiered-land")
                target = self.add(game.engine, "Generic Spree Artifact", seat="B", ref="tiered-target")
                late = self.add(game.engine, "Generic Bound Plains", zone="hand", ref="tiered-late-land")
                private = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="tiered-private")
                self.assert_closed(game, source)
                self.prepare(game, mana={"W": 2, "C": 3})
                self.checkpoint(game)
                action = self.action(game, source)
                offers = {tuple(row["target_schema"]["legal_modes"]): row for row in action["cost_options"]}
                self.assertEqual({(key,) for key, _, _, _ in payments}, set(offers))
                for key, price, name, _ in payments:
                    offer = offers[(key,)]
                    self.assertIn(name, offer["label"])
                    expected = {("GENERIC" if symbol == "C" else symbol): amount for symbol, amount in price.items()}
                    self.assertEqual(expected, {symbol: amount for symbol, amount in offer["requirements"].items() if amount})
                refs = [] if mode == "mode_3" else [target.ref]
                options = {"modes": [mode], "targets": refs, "cost_option": offers[(mode,)]["id"]}
                self.reject(game, action["id"], modes=[], targets=refs, pay="auto")
                self.reject(game, action["id"], modes=["mode_1", "mode_2"], targets=[target.ref], pay="auto")
                self.reject(game, action["id"], modes=["unknown"], targets=refs, pay="auto")
                self.reject(game, action["id"], "D", **options, pay="auto")
                wrong = tuple(key for key, _, _, _ in payments if key != mode)[0]
                self.reject(game, action["id"], **{**options, "cost_option": offers[(wrong,)]["id"]}, pay="auto")
                self.reject(game, action["id"], **{**options, "targets": [target.ref] if mode == "mode_3" else []}, pay="auto")
                self.reject(game, action["id"], **options, pay="manual", payment={})
                self.cast(game, source, **options, pay="manual", payment=payment)
                self.assertEqual([mode], game.state.stack[-1].modes)
                self.assertEqual(refs, game.state.stack[-1].targets)
                self.assertEqual(1, game.engine.card_record(game.state.cards[source.object_id]).mana_value)
                self.assertEqual(5 - sum(payment.values()), sum(game.state.players["A"].mana_pool.values()))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40 + life, game.state.players["A"].life)
                self.assertEqual(mode != "mode_3", self.protects(game, target))
                self.assertEqual(mode == "mode_3", self.protects(game, ally))
                self.assertEqual(mode == "mode_3", self.protects(game, land))
                self.priority(game, "A")
                entry = game.act("pilot:A", {"action_id": f"play-land:{late.ref}"})
                self.assertTrue(entry.ok, entry.summary)
                self.assertFalse(self.protects(game, late))
                for seat in "ABD":
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.replay(game, load=True)

    def test_restoration_magic_illegal_cura_target_prevents_life_gain(self):
        game = self.session(70218310)
        source = self.add(game.engine, "Restoration Magic", zone="hand", ref="tiered-cura")
        target = self.add(game.engine, "Generic Bound Body", seat="B", ref="tiered-cura-target")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="tiered-bounce")
        private = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="tiered-cura-private")
        game.state.players["B"].mana_pool["U"] = 1
        self.prepare(game, mana={"W": 1, "C": 1})
        self.checkpoint(game)
        self.cast(game, source, modes=["mode_2"], targets=[target.ref], pay="auto")
        self.priority(game, "B")
        self.cast(game, bounce, "B", targets=[target.ref], pay="auto")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertFalse(self.protects(game, target))
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_restoration_magic_free_base_keeps_the_tiered_addition(self):
        game = self.session(70218311)
        source = self.add(game.engine, "Restoration Magic", zone="hand", ref="tiered-free-magic")
        giver = self.add(game.engine, "Omniscience", ref="tiered-free-giver")
        self.assert_closed(game, giver)
        self.prepare(game, mana={"W": 1, "C": 3})
        self.checkpoint(game)
        action = self.action(game, source)
        grant = next(row for row in action["cost_options"] if row["id"].startswith("battlefield-alternative:")
                     and row["target_schema"]["legal_modes"] == ["mode_3"])
        self.assertEqual({"W": 1, "GENERIC": 3}, {symbol: amount for symbol, amount in grant["requirements"].items() if amount})
        cast = {"modes": ["mode_3"], "targets": [], "cost_option": grant["id"]}
        self.reject(game, action["id"], **cast, pay="manual", payment={})
        self.reject(game, action["id"], **cast, pay="manual", payment={"C": 3})
        self.cast(game, source, **cast, pay="manual", payment={"W": 1, "C": 3})
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(46, game.state.players["A"].life)
        self.assertTrue(self.protects(game, giver))
        self.replay(game, load=True)

    def test_restoration_magic_copy_retains_its_mode_for_the_new_controller(self):
        game = self.session(70218312)
        source = self.add(game.engine, "Restoration Magic", zone="hand", ref="tiered-copy-original")
        twin = self.add(game.engine, "Twincast", seat="B", zone="hand", ref="tiered-copy-spell")
        own = self.add(game.engine, "Generic Bound Plains", ref="tiered-original-land")
        other = self.add(game.engine, "Generic Bound Plains", seat="B", ref="tiered-copy-land")
        self.assert_closed(game, twin)
        game.state.players["B"].mana_pool["U"] = 2
        self.prepare(game, mana={"W": 2, "C": 3})
        self.checkpoint(game)
        self.cast(game, source, modes=["mode_3"], targets=[], pay="auto")
        original = game.state.stack[-1].ref
        self.priority(game, "B")
        self.cast(game, twin, "B", targets=[original], pay="auto")
        for _ in range(12):
            if game.state.stack and game.state.stack[-1].kind == "spell_copy":
                break
            result = game.act(game.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        copied = game.state.stack[-1]
        self.assertEqual(("spell_copy", "B", ["mode_3"], []),
                         (copied.kind, copied.controller, copied.modes, copied.targets))
        self.assertEqual(0, sum(game.state.players["B"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((46, 46), (game.state.players["A"].life, game.state.players["B"].life))
        self.assertTrue(self.protects(game, own) and self.protects(game, other))
        self.replay(game, load=True)

    def test_tiered_schema_and_runtime_dependency_withdrawal(self):
        from unittest.mock import patch

        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.spree_model import TIERED_FIXED_CAPABILITY

        card = replace(record(RESTORATION_TEXT, "Instant"), mana_cost="{W}", mana_value=1, keywords=("Tiered",))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(RESTORATION_TEXT)), (node.span.start, node.span.end))
        self.assertEqual((1, 1), (node.target_schema["min_modes"], node.target_schema["max_modes"]))
        self.assertIn(TIERED_FIXED_CAPABILITY, node.capability_dependencies)
        self.assertEqual({"mode_1": "Cure", "mode_2": "Cura", "mode_3": "Curaga"}, node.cost["tiered"]["mode_labels"])
        for malformed in (
            {"spree": node.cost["tiered"]},
            {"tiered": {**node.cost["tiered"], "schema_version": True}},
            {**node.cost, "spree": node.cost["tiered"]},
        ):
            self.assertFalse(capability_dependencies_for_node(effects=node.effects, target_schema=node.target_schema,
                mechanic_ids=node.mechanics, cost_schema=malformed))
        for text in (RESTORATION_TEXT.replace("{1}", "{X}"), RESTORATION_TEXT.replace("{1}", "{W/U}"),
                     RESTORATION_TEXT.replace("You gain 6 life.", "Rewrite history.")):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry).status)
        with patch("quorune.compiler.whole_modal_templates.fixed_tiered_modal_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)
        game = self.session(70218313)
        source = self.add(game.engine, "Restoration Magic", zone="hand")
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(game.engine.semantic_program_is_current_trusted(program))
        deps = tuple(value for value in program.capability_dependencies if value != TIERED_FIXED_CAPABILITY)
        mutant = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{TIERED_FIXED_CAPABILITY}", binding["blockers"])



class OwnCombatSpreeCompilerTests(unittest.TestCase):
    compile = SpreeModeCostCompilerTests.compile

    def test_own_combat_spree_keeps_each_typed_mode_and_its_independent_price(self):
        from unittest.mock import patch
        from quorune.extra_combat_model import EXTRA_COMBAT_FOLLOWUP_FIELD
        from quorune.rules.controlled_combat_tokens import COMBAT_TOKEN_FIELD

        text = ("Spree\n+ {2}{R} — Untap all creatures you control. If it's your combat phase, "
                "there is an additional combat phase after this phase.\n"
                "+ {2} — Creatures you control get +1/+0 and gain first strike until end of turn.\n"
                "+ {R} — Choose target opponent. Whenever a creature you control deals combat damage "
                "to that player this turn, create a tapped Treasure token.")

        def witness():
            ir = self.compile(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            modes = node.target_schema["modes"]
            self.assertEqual(3, modes["mode_1"]["effects"][0][EXTRA_COMBAT_FOLLOWUP_FIELD]["schema_version"])
            self.assertEqual({"power": 1, "toughness": 0, "keywords": ["First Strike"]},
                             {k: modes["mode_2"]["effects"][0][k] for k in ("power", "toughness", "keywords")})
            self.assertTrue(modes["mode_3"]["effects"][0][COMBAT_TOKEN_FIELD])
            self.assertEqual("opponent", modes["mode_3"]["groups"][0]["player_relation"])
            for index, price in enumerate(("{2}{R}", "{2}", "{R}"), 1):
                self.assertEqual(mana_cost_to_vector(price)[0], node.cost["spree"]["mode_costs"][f"mode_{index}"])

        witness()
        with patch("quorune.compiler.delayed_draw_templates.controlled_player_damage_token_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for altered in (text.replace("your combat phase", "a combat phase"),
                        text.replace("target opponent", "target player"),
                        text.replace("tapped Treasure", "Treasure"),
                        text.replace("combat damage", "damage")):
            self.assertNotEqual("exact", self.compile(altered).status)
