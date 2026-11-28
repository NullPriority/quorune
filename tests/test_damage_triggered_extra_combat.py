"""Native evidence for one source combat-damage untap and additional combat."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler import trigger_result_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.extra_combat_model import (
    EXTRA_COMBAT_CAPABILITY, EXTRA_COMBAT_FOLLOWUP_FIELD, EXTRA_COMBAT_PHASES_FIELD,
    ExtraCombatUntap, decode_extra_combat_effect, extra_combat_effect,
)
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PORT = "Port Razer"
BLOOD = "Bloodthirster"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = (PORT, BLOOD)


class DamageTriggeredExtraCombatTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-restriction-material-cards.json": {
                "Unsummon", "Cloudshift", "Grizzly Bears", "Sol Ring", "Plains", "Island",
            },
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "scheduled-turn-skip-cards.json": {"Stonehorn Dignitary"},
            "self-loyalty-instant-permission-cards.json": {"The Wandering Emperor"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = [FIXTURES / "damage-triggered-extra-combat-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, name
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / name
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "damage-triggered-extra-combat.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Extra combat", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _ready_source(self, game, name):
        source = self.add(game.engine, name)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def _combat_damage_trigger(self, game, source, *, index=0):
        self.until(game, lambda g: self.trigger_count(g, source) == 1)
        self.assertEqual(("combat", "combat_damage", index),
                         (game.state.phase, game.state.step, game.state.combat.damage_step_index))
        return game.state.combat.damage_sequence_id

    def _next_attack(self, game, previous_combat):
        self.until(game, lambda g: g.state.pending_decision is not None
                   and g.state.pending_decision.kind == "combat.attackers"
                   and g.state.combat.damage_sequence_id != previous_combat)
        return game.state.combat.damage_sequence_id

    @staticmethod
    def _started_combats(game, turn_sequence):
        return [e for e in game.state.events if e.code == "step.begin"
                and e.step == "beginning_combat" and e.turn_sequence == turn_sequence]

    def test_exact_wire_and_empty_phase_record_keep_the_established_shape(self):
        for mode in ExtraCombatUntap:
            with self.subTest(mode=mode):
                descriptor = extra_combat_effect(mode)
                self.assertIs(mode, decode_extra_combat_effect(descriptor))
                for malformed in (
                    None,
                    dict(descriptor, **{EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": True}}),
                    dict(descriptor, op="tap"),
                    dict(descriptor, phase="main"),
                ):
                    with self.assertRaises(ValueError):
                        decode_extra_combat_effect(malformed)
                if mode is ExtraCombatUntap.SOURCE:
                    resolved = {**descriptor, EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 1, "source_card": None}}
                    self.assertIs(mode, decode_extra_combat_effect(resolved, resolved=True))
                    malformed = {**descriptor, EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 1, "source_card": "$target.0"}}
                    with self.assertRaises(ValueError):
                        decode_extra_combat_effect(malformed)
        game = self.session(4821900)
        raw = game.state.to_dict()
        self.assertNotIn(EXTRA_COMBAT_PHASES_FIELD, raw)
        restored = GameState.from_dict(raw)
        self.assertEqual(raw, restored.to_dict())
        self.assertEqual(authoritative_state_hash(game.state), authoritative_state_hash(restored))
        for malformed in (0, -1, True, None, "2"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                GameState.from_dict(dict(raw, **{EXTRA_COMBAT_PHASES_FIELD: malformed}))

    def test_complete_originals_keep_damage_untap_and_attack_history_siblings(self):
        for name, untap in ((PORT, ExtraCombatUntap.CONTROLLED_CREATURES), (BLOOD, ExtraCombatUntap.SOURCE)):
            with self.subTest(name=name):
                record, programs = self._original(name)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                trigger, = [p for p in programs if p.event == "damage.dealt.self"]
                self.assertIn(EXTRA_COMBAT_CAPABILITY, trigger.capability_dependencies)
                result, = trigger.effects
                self.assertEqual(extra_combat_effect(untap), dict(result))
                self.assertTrue(any(p.event != trigger.event and p.active_zone == "battlefield" for p in programs))
                missing = tuple(value for value in trigger.capability_dependencies if value != EXTRA_COMBAT_CAPABILITY)
                from dataclasses import replace
                from quorune.card_programs.binding import bind_semantic_program_runtime

                altered = replace(trigger, capability_dependencies=missing,
                                  capability_closure=self.registry.closure(missing, profile=PROFILE).to_dict())
                rejected = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(rejected["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + EXTRA_COMBAT_CAPABILITY, rejected["blockers"])

    def test_actual_port_razer_untaps_its_creatures_and_keeps_direct_player_history(self):
        game = self.session(4821910)
        source = self._ready_source(game, PORT)
        ally = self.add(game.engine, "Grizzly Bears")
        outsider = self.add(game.engine, "Grizzly Bears", seat="B", ref="extra-combat-other-bear")
        artifact = self.add(game.engine, "Sol Ring")
        walker = self.add(game.engine, "The Wandering Emperor", seat="B")
        private = self.add(game.engine, "Sol Ring", seat="D", zone="hand", ref="extra-combat-private-d")
        walker.counters["loyalty"] = 3
        for card in (ally, outsider, artifact):
            card.tapped = True
        self.prepare(game)
        self.checkpoint(game)
        turn = game.state.turn_sequence
        turn_id = game.state.current_turn.turn_id
        self.attack(game, [(source, "B")])
        first = self._combat_damage_trigger(game, source)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual([False, False, True, True], [
            game.state.cards[card.object_id].tapped for card in (source, ally, outsider, artifact)])
        self.assertEqual(1, len(self._started_combats(game, turn)))
        game = self.replay(game, load=True)
        prior_starts = len(self._started_combats(game, turn))
        second = self._next_attack(game, first)
        self.assertNotEqual(first, second)
        self.assertEqual((turn_id, turn, "A"), (game.state.current_turn.turn_id, game.state.turn_sequence, game.state.active_player))
        self.assertEqual(0, game.state.extra_combat_phases)
        domains = game.engine._attack_declaration_problem("A").domains[source.ref]
        self.assertNotIn("B", domains)
        self.assertIn("C", domains)
        self.assertIn(walker.ref, domains)
        before = authoritative_state_hash(game.state)
        bad = {"action_id": "attack", "attackers": {source.ref: "B"}}
        self.assertFalse(game.act("pilot:A", bad).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.attack(game, [(source, walker.ref)])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        hit, = (
            row for event in game.state.events if event.code == "combat.damage"
            for row in event.details["damage_events"]
            if row["source"] == source.ref and row["target"] == walker.ref
        )
        self.assertEqual((4, "permanent"), (hit["amount"], hit["target_kind"]))
        self.assertIn("planeswalker", hit["target_types"])
        self.assertEqual(0, game.state.extra_combat_phases)
        self.assertEqual(prior_starts + 1, len(self._started_combats(game, turn)))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_bloodthirster_keeps_both_damage_grants_through_stun_and_one_combat_skip(self):
        game = self.session(4821920)
        source = self._ready_source(game, BLOOD)
        source.counters["stun"] = 1
        ally = self.add(game.engine, "Grizzly Bears")
        ally.tapped = True
        strobe = self.add(game.engine, "Assault Strobe", zone="hand")
        dignitary = self.add(game.engine, "Stonehorn Dignitary", seat="B")
        dignitary.tapped = True
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", strobe, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertLessEqual({"flying", "trample", "double strike"}, set(game.engine._combat_keywords(source)))
        self.attack(game, [(source, "B")])
        combat_id = self._combat_damage_trigger(game, source, index=0)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertTrue(current.tapped)
        self.assertEqual(0, current.counters.get("stun", 0))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual(combat_id, self._combat_damage_trigger(game, source, index=1))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.extra_combat_phases)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertTrue(game.state.cards[ally.object_id].tapped)
        turn = game.state.turn_sequence
        self.assertEqual(2, game.state.to_dict()[EXTRA_COMBAT_PHASES_FIELD])
        self.assertEqual(1, len(self._started_combats(game, turn)))
        game = self.replay(game, load=True)
        prior_starts = len(self._started_combats(game, turn))
        self.cast(game, "B", blink, [dignitary.ref])
        self.target(game, "B", ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.extra_combat_phases)
        grant, = game.state.scheduled_turn_skips
        self.assertEqual(("A", "combat"), (grant.player, grant.spec.boundary.value))
        new_combat = self._next_attack(game, combat_id)
        self.assertNotEqual(combat_id, new_combat)
        self.assertEqual(0, game.state.extra_combat_phases)
        self.assertFalse(game.state.scheduled_turn_skips)
        self.assertNotIn(EXTRA_COMBAT_PHASES_FIELD, game.state.to_dict())
        self.attack(game, [])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual(turn, game.state.turn_sequence)
        self.assertEqual(prior_starts + 1, len(self._started_combats(game, turn)))
        self.replay(game, load=True)

    def test_actual_bloodthirster_still_adds_the_combat_after_its_source_leaves(self):
        game = self.session(4821930)
        source = self._ready_source(game, BLOOD)
        companion = self.add(game.engine, "Grizzly Bears")
        companion.acquired_control_turn_count = -1
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        before = self._combat_damage_trigger(game, source)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.extra_combat_phases)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self._next_attack(game, before)
        self.assertEqual(0, game.state.extra_combat_phases)
        prior_life = game.state.players["C"].life
        self.attack(game, [(companion, "C")])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual(prior_life - 2, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_omitting_the_exact_combat_result_restores_both_original_residuals(self):
        with patch.object(trigger_result_templates, "_extra_combat_after_source_damage_result", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual(["trigger_effect"], [row.kind for row in ir.material_residuals])
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_combat_phase_authority_withdrawal_denies_both_complete_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(registry_data)
        next(row for row in document["capabilities"] if row["id"] == EXTRA_COMBAT_CAPABILITY).update(
            status="blocked", blockers=["Scoped extra combat authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((EXTRA_COMBAT_CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{EXTRA_COMBAT_CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Sol Ring")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


FULL_THROTTLE = "Full Throttle"
FULL_THROTTLE_TEXT = (
    "After this main phase, there are two additional combat phases.\n"
    "At the beginning of each combat this turn, untap all creatures that attacked this turn."
)


class MainPhaseCombatNativeTests(InheritedNativeFixture, unittest.TestCase):
    _started_combats = staticmethod(DamageTriggeredExtraCombatTests._started_combats)

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-restriction-material-cards.json": {"Grizzly Bears", "Cloudshift"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "ability-source-counter-cards.json": {"Vedalken Orrery"},
            "additional-beginning-originals-cards.json": {"Sphinx of the Second Sun"},
            "external-entry-tapped-cards.json": {"Thalia, Heretic Cathar"},
            "per-opponent-attacking-token-cards.json": {"Adeline, Resplendent Cathar"},
            "self-public-zone-cards.json": {"Mountain"},
        }
        parts = [FIXTURES / "damage-triggered-extra-combat-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, name
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "main-phase-combats.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Main-phase combat", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def spell(self, game, seat, *, flash=False, lands=False):
        source = self.add(game.engine, FULL_THROTTLE, seat=seat, zone="hand")
        self.assert_closed(game, source)
        if flash:
            orrery = self.add(game.engine, "Vedalken Orrery", seat=seat)
            self.assert_closed(game, orrery)
        if lands:
            for index in range(6):
                self.add(game.engine, "Mountain", seat=seat, ref=f"throttle-payment-{seat}-{index}")
        return source

    def test_seize_main_groups_and_beginning_phases_keep_lifo_and_the_original_successor(self):
        from quorune.rules.main_phase_combat_model import MainPhaseCombatSchedule

        game = self.session(4821947)
        source = self.add(game.engine, "Seize the Day", zone="hand")
        sphinx = self.add(game.engine, "Sphinx of the Second Sun")
        orrery = self.add(game.engine, "Vedalken Orrery")
        original_target = self.add(game.engine, "Grizzly Bears", ref="seize-early-target")
        other_creature = self.add(game.engine, "Grizzly Bears", ref="seize-untargeted")
        original_target.tapped = True
        other_creature.tapped = True
        original_target.acquired_control_turn_count = -1
        for card in (source, sphinx, orrery):
            self.assert_closed(game, card)
        for index in range(4):
            self.add(game.engine, "Mountain", ref=f"seize-sphinx-payment-{index}")
        self.prepare(game)
        self.checkpoint(game)
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.phase == "postcombat_main"
                   and bool(current.state.stack)
                   and current.state.stack[-1].source_object_id == sphinx.object_id
                   and current.state.priority_player == "A")
        self.cast(game, "A", source, [original_target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[original_target.object_id].tapped)
        self.assertTrue(game.state.cards[other_creature.object_id].tapped)
        self.assertEqual(MainPhaseCombatSchedule("postcombat_main", ("beginning", "combat", "main")),
                         game.state.main_phase_combat_schedule)
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.phase == "beginning"
                   and current.state.step == "upkeep")
        self.assertEqual(turn, game.state.turn_sequence)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        self.assertEqual(turn, game.state.turn_sequence)
        self.attack(game, [])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.until(game, lambda current: current.state.phase == "ending" and current.state.step == "end_step")
        self.assertEqual(turn, game.state.turn_sequence)
        self.assertIsNone(game.state.main_phase_combat_schedule)
        self.replay(game, load=True)

    def test_actual_seize_hand_and_flashback_insert_two_pairs_and_replay(self):
        from quorune.rules.main_phase_combat_model import MainPhaseCombatSchedule

        s=self.session(4821950)
        e=s.engine
        spell=self.add(e,"Seize the Day",zone="hand")
        helper=self.add(e,"Grizzly Bears")
        helper.acquired_control_turn_count=-1
        helper.tapped=True
        for idx in range(5):
            self.add(e,"Generic Bound Plains",ref=f"seize-cost-{idx}")
        for idx in range(2):
            self.add(e,"Mountain",ref=f"seize-red-{idx}")
        self.assert_closed(s,spell)
        self.prepare(s)
        self.checkpoint(s)
        active_turn=s.state.turn_sequence
        self.cast(s,"A",spell,[helper.ref])
        self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[helper.object_id].tapped)
        self.assertEqual(MainPhaseCombatSchedule("precombat_main", ("combat", "main")),
                         s.state.main_phase_combat_schedule)
        s=self.replay(s,load=True)
        self.until(s,lambda g:g.state.phase=="postcombat_main"
                   and g.state.pending_decision is not None
                   and g.state.pending_decision.kind=="priority"
                   and g.state.priority_player=="A")
        self.assertEqual(1,len(self._started_combats(s,active_turn)))
        rows=s.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"]
        offer=next(row for row in rows if row.get("card")==spell.ref)
        alternative=next(row for row in offer["cost_options"] if "flashback" in row["id"])
        result=s.act("pilot:A",{"action_id":offer["id"],"cost_option":alternative["id"],"targets":[helper.ref],"pay":"auto"})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual("exile",s.state.cards[spell.object_id].zone)
        retained = s.state.main_phase_combat_schedule
        s=self.replay(s,load=True)
        self.assertEqual(retained,s.state.main_phase_combat_schedule)
        for inserted in (True, False):
            self.until(s,lambda g:g.state.pending_decision is not None
                       and g.state.pending_decision.kind=="combat.attackers")
            self.assertEqual(active_turn,s.state.turn_sequence)
            self.assertEqual(inserted,s.state.main_phase_combat_schedule is not None)
            result=s.act("pilot:A",{"action_id":"attack","attackers":{}})
            self.assertTrue(result.ok,result.summary)
            self.until(s,lambda g:g.state.phase=="postcombat_main")
        self.until(s,lambda g:g.state.phase=="ending" and g.state.step=="end_step")
        self.assertIsNone(s.state.main_phase_combat_schedule)
        self.replay(s,load=True)

    def untap_trigger(self, game, *, controller):
        self.until(game, lambda g: bool(g.state.stack and g.state.stack[-1].context.get("delayed_trigger_ref")))
        item = game.state.stack[-1]
        self.assertEqual(("combat", "beginning_combat"), (game.state.phase, game.state.step))
        self.assertEqual((controller, "triggered_ability", []), (item.controller, item.kind, item.targets))
        return item

    def finish_a_combat_without_attacking(self, game):
        self.attack(game, [])
        self.until(game, lambda g: g.state.step == "end_combat")

    def test_actual_opponent_main_adds_two_combats_and_untaps_declared_incarnations(self):
        from quorune.rules.main_phase_combat_model import MainPhaseCombatSchedule

        game = self.session(50081201)
        source = self.spell(game, "B", flash=True)
        adeline = self.add(game.engine, "Adeline, Resplendent Cathar", ref="throttle-token-maker")
        thalia = self.add(game.engine, "Thalia, Heretic Cathar", seat="B", ref="throttle-tapped-entry")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Generic Bound Plains")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="throttle-hidden-ref")
        staying = self.add(game.engine, "Grizzly Bears", ref="throttle-stays")
        returning = self.add(game.engine, "Grizzly Bears", ref="throttle-blinks")
        owned_by_caster = self.add(game.engine, "Grizzly Bears", seat="B", ref="throttle-caster-bear")
        staying.acquired_control_turn_count = returning.acquired_control_turn_count = -1
        owned_by_caster.tapped = True
        for card in (adeline, thalia, blink):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 4, "R": 2})
        self.checkpoint(game)
        turn = game.state.turn_sequence
        turn_id = game.state.current_turn.turn_id
        self.cast_for(game, "B", source)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(MainPhaseCombatSchedule("precombat_main", ("combat", "combat")), game.state.main_phase_combat_schedule)
        self.assertEqual("B", game.state.delayed_triggers[-1].controller)
        game = self.replay(game, load=True)

        self.untap_trigger(game, controller="B")
        first = game.state.combat.damage_sequence_id
        self.assertEqual("A", game.state.active_player)
        self.resolve_top(game)
        self.attack(game, [(staying, "B"), (returning, "C")])
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda g: g.state.step == "end_combat")
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(3, len(tokens))
        self.assertTrue(all(card.tapped for card in tokens))
        original_identity = game.state.cards[returning.object_id].logical_object_id
        self.cast_for(game, "A", blink, [returning.ref])
        self.assertIsNone(self.resolve(game))
        reentered = game.state.cards[returning.object_id]
        self.assertNotEqual(original_identity, reentered.logical_object_id)
        self.assertTrue(reentered.tapped)

        self.untap_trigger(game, controller="B")
        second = game.state.combat.damage_sequence_id
        self.assertTrue(game.state.cards[staying.object_id].tapped)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[staying.object_id].tapped)
        self.assertTrue(game.state.cards[returning.object_id].tapped)
        self.assertTrue(game.state.cards[owned_by_caster.object_id].tapped)
        self.assertTrue(all(game.state.cards[card.object_id].tapped for card in tokens))
        self.finish_a_combat_without_attacking(game)
        self.untap_trigger(game, controller="B")
        third = game.state.combat.damage_sequence_id
        self.assertEqual(3, len({first, second, third}))
        self.assertIsNone(game.state.main_phase_combat_schedule)
        self.assertEqual(("A", turn_id, turn), (game.state.active_player, game.state.current_turn.turn_id, game.state.turn_sequence))
        self.resolve_top(game)
        self.finish_a_combat_without_attacking(game)
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual(turn, game.state.turn_sequence)
        self.until(game, lambda g: g.state.turn_sequence > turn)
        self.assertFalse(game.state.delayed_triggers[-1].active)
        self.replay(game, load=True)

    def test_actual_outside_main_only_creates_the_future_trigger(self):
        game = self.session(50081202)
        source = self.spell(game, "B", flash=True, lands=True)
        blood = self.add(game.engine, BLOOD)
        bear = self.add(game.engine, "Grizzly Bears")
        blood.acquired_control_turn_count = bear.acquired_control_turn_count = -1
        self.assert_closed(game, blood)
        self.prepare(game)
        self.checkpoint(game)
        turn = game.state.turn_sequence
        self.attack(game, [(blood, "C"), (bear, "D")])
        self.until(game, lambda g: self.trigger_count(g, blood) == 1)
        first_combat = game.state.combat.damage_sequence_id
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.until(game, lambda g: g.state.step == "end_combat")
        self.cast_for(game, "B", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertIsNone(game.state.main_phase_combat_schedule)
        self.assertEqual(1, len(self._started_combats(game, turn)))
        self.assertTrue(game.state.cards[bear.object_id].tapped)
        game = self.replay(game, load=True)
        self.untap_trigger(game, controller="B")
        self.assertNotEqual(first_combat, game.state.combat.damage_sequence_id)
        self.assertEqual(0, game.state.extra_combat_phases)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[bear.object_id].tapped)
        self.finish_a_combat_without_attacking(game)
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual(turn, game.state.turn_sequence)
        self.replay(game, load=True)

    def test_actual_postcombat_orders_current_boundary_phases_by_creation(self):
        for earlier_beginning in (False, True):
            with self.subTest(earlier_beginning=earlier_beginning):
                game = self.session(50081210 + earlier_beginning)
                source = self.spell(game, "A", flash=True, lands=True)
                sphinx = self.add(game.engine, "Sphinx of the Second Sun")
                self.assert_closed(game, sphinx)
                self.prepare(game)
                self.checkpoint(game)
                turn = game.state.turn_sequence
                self.until(game, lambda g: g.state.phase == "postcombat_main" and self.trigger_count(g, sphinx) == 1)
                if earlier_beginning:
                    self.resolve_top(game)
                self.cast_for(game, "A", source)
                self.resolve_top(game)
                if not earlier_beginning:
                    self.resolve_top(game)
                expected = ("combat", "combat", "beginning") if earlier_beginning else ("beginning", "combat", "combat")
                self.assertEqual(expected, game.state.main_phase_combat_schedule.pending)
                game = self.replay(game, load=True)
                first_schedule_event = len(game.state.events)
                for _ in range(2):
                    self.untap_trigger(game, controller="A")
                    self.resolve_top(game)
                    self.finish_a_combat_without_attacking(game)
                self.until(game, lambda g: g.state.step == "end_step")
                self.assertEqual(turn, game.state.turn_sequence)
                self.assertIsNone(game.state.main_phase_combat_schedule)
                self.assertIsNone(game.state.additional_beginning_phases)
                steps = [row.step for row in game.state.events[first_schedule_event:] if row.code == "step.begin"
                         and row.step in {"untap", "beginning_combat", "main", "end_step"}]
                self.assertEqual([*({"combat": "beginning_combat", "beginning": "untap"}[phase] for phase in expected),
                                  "end_step"], steps)
                self.replay(game, load=True)

    def test_exact_whole_spell_rejects_changed_scope_and_withdrawn_authority(self):
        from dataclasses import replace

        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler import main_phase_combats
        from quorune.rules.main_phase_combat_model import (
            ATTACK_UNTAP_CAPABILITY, MAIN_COMBAT_CAPABILITY, SCHEDULE_FIELD, attack_untap_effect, main_phase_combat_effect,
        )
        from quorune.rules.main_phase_combats import main_phase_combat_node_capabilities, main_phase_combat_payload_dependencies
        from test_granted_skulk import record

        sample = record(FULL_THROTTLE_TEXT, "Sorcery")
        oracle = compile_oracle_card(sample, capability_registry=self.registry)
        self.assertEqual("exact", oracle.status, oracle.material_residuals)
        node, = oracle.faces[0].nodes
        self.assertEqual((0, len(FULL_THROTTLE_TEXT)), (node.span.start, node.span.end))
        self.assertEqual((main_phase_combat_effect(),), node.effects)
        for text in (
            FULL_THROTTLE_TEXT.replace("two additional", "three additional"),
            FULL_THROTTLE_TEXT.replace("this main phase", "this combat phase"),
            FULL_THROTTLE_TEXT.replace("all creatures that attacked", "creatures you control that attacked"),
            FULL_THROTTLE_TEXT.splitlines()[0],
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(sample, oracle_text=text), capability_registry=self.registry).status)
        with patch.object(main_phase_combats, "compile_main_phase_combat_face", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(sample, capability_registry=self.registry).status)
        for invalid in ({**main_phase_combat_effect(), "count": 2}, attack_untap_effect()):
            self.assertEqual((), main_phase_combat_node_capabilities((invalid,), None, None))
        other_combat_instructions = (
            {"op": "set_public_tap_state", "tapped": False, EXTRA_COMBAT_FOLLOWUP_FIELD:
                {"schema_version": 2, "exclude_source_card": "$source.zone_object"}},
            {"op": "set_public_tap_state", "tapped": False, EXTRA_COMBAT_FOLLOWUP_FIELD:
                {"schema_version": 3, "own_combat_only": True}},
        )
        for effect in other_combat_instructions:
            self.assertEqual((), main_phase_combat_payload_dependencies(effect))
            self.assertIsNone(main_phase_combat_node_capabilities((effect,), None, None))
        game = self.session(50081203)
        raw = game.state.to_dict()
        self.assertNotIn(SCHEDULE_FIELD, raw)
        for invalid in (True, {"main_phase": "combat", "pending": ["combat"], "active_phase": None},
                        {"main_phase": "precombat_main", "pending": ["beginning"], "active_phase": None}):
            with self.assertRaises(ValueError):
                GameState.from_dict({**raw, SCHEDULE_FIELD: invalid})
        source = self.spell(game, "A")
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        for missing in (MAIN_COMBAT_CAPABILITY, ATTACK_UNTAP_CAPABILITY):
            deps = tuple(cap for cap in program.capability_dependencies if cap != missing)
            mutant = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{missing}", bound["blockers"])


if __name__ == "__main__":
    unittest.main()


MORAUG = "Moraug, Fury of Akoum"


class MoraugCompilerTests(unittest.TestCase):
    def test_strict_descriptor_records_both_sides_and_rejects_off_event(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.extra_combat_model import (
            MAIN_PHASE_EXTRA_COMBAT_CAPABILITY, MainPhaseCombatSchedule, MainPhaseInsertion,
            main_phase_extra_combat_condition,
        )
        from quorune.rules.effect_node_capabilities import main_phase_extra_combat_program_dependencies
        from quorune.semantic_runtime.quantified_anthems import (
            RECIPIENT_ATTACK_ANTHEM_CAPABILITY, recipient_attack_anthem_descriptor,
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "moraug-source.sqlite3"
            build_fixture_database(FIXTURES / "damage-triggered-extra-combat-cards.json", path)
            with CardDatabase(path) as db:
                registry = load_default_capability_registry()
                card = db.lookup(MORAUG, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(db, registry, MORAUG)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                anthem, = [p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "characteristics.evaluate"]
                landfall, = [p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "land.enter"]
                self.assertEqual(recipient_attack_anthem_descriptor(), dict(anthem.handlers[0]))
                self.assertEqual(main_phase_extra_combat_condition(), landfall.event_condition)
                expected = extra_combat_effect(ExtraCombatUntap.GRANTED_COMBAT_CREATURES)
                self.assertEqual([expected], landfall.effects)
                for program, withheld in ((anthem, RECIPIENT_ATTACK_ANTHEM_CAPABILITY),
                                            (landfall, MAIN_PHASE_EXTRA_COMBAT_CAPABILITY)):
                    dependencies = [cap for cap in program.capability_dependencies if cap != withheld]
                    altered = replace(program, capability_dependencies=dependencies,
                                      capability_closure=registry.closure(dependencies, profile=PROFILE).to_dict())
                    result = bind_semantic_program_runtime(altered, capability_registry=registry, profile=PROFILE)
                    self.assertFalse(result["strict"], result)
                    self.assertIn("capability:undeclared_runtime_dependency:" + withheld, result["blockers"])
                for altered in (replace(landfall, event="permanent.enter"),
                                replace(landfall, event_condition=None),
                                replace(landfall, coverage=[entry for entry in landfall.coverage if entry != "intervening_condition"])):
                    self.assertRaises(ValueError, main_phase_extra_combat_program_dependencies, altered)
                    result = bind_semantic_program_runtime(altered, capability_registry=registry, profile=PROFILE)
                    self.assertFalse(result["strict"], result)
                for word in ("own combat phase", "upkeep"):
                    altered = replace(card, oracle_text=card.oracle_text.replace("your main phase", word))
                    result = compile_oracle_card(altered, capability_registry=registry, capability_profile=PROFILE)
                    self.assertNotEqual("exact", result.status)
        cooked = MainPhaseCombatSchedule("postcombat_main", 1, (
            MainPhaseInsertion("combat", "S8"), MainPhaseInsertion("beginning"), MainPhaseInsertion("combat", "S6"),
        ))
        self.assertEqual(cooked, MainPhaseCombatSchedule.from_dict(cooked.to_dict()))
        with self.assertRaises(ValueError):
            MainPhaseCombatSchedule.from_dict({**cooked.to_dict(), "schema_version": True})


class MoraugNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os

        cls.temporary = TemporaryDirectory()
        directory = Path(cls.temporary.name)
        database = directory / "moraug-native.sqlite3"
        pinned = os.environ.get("MORAUG_PINNED_DB")
        if not pinned:
            support = {
                "source-attack-alone-cards.json": {
                    "Act of Treason", "Forest", "Grizzly Bears", "Island", "Isamaru, Hound of Konda",
                    "Plains", "Unsummon", "Vedalken Orrery",
                },
                "activation-restriction-material-cards.json": {"Cloudshift"},
                "library-search-rules-cards.json": {"Evolving Wilds"},
                "scryfall-exact-lists.json": {"Mountain"},
                "additional-beginning-originals-cards.json": {"Sphinx of the Second Sun"},
                "ending-turn-effect-cards.json": {"Sundial of the Infinite"},
            }
            pieces = [FIXTURES / "damage-triggered-extra-combat-cards.json"]
            for filename, wanted in support.items():
                document = json.loads((FIXTURES / filename).read_bytes())
                document["cards"] = [row for row in document["cards"] if row["name"] in wanted]
                assert wanted == {row["name"] for row in document["cards"]}, filename
                identities = {row["oracle_id"] for row in document["cards"]}
                document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in identities]
                piece = directory / filename
                piece.write_text(json.dumps(document), encoding="utf-8")
                pieces.append(piece)
            build_fixture_database(pieces, database)
        cls.db = CardDatabase(pinned or database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Main-phase combat witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @staticmethod
    def _current(game, card):
        return game.state.cards[card.object_id]

    def _power(self, game, card):
        return game.engine._numeric_stat(card.object_id, "power")

    def _source(self, game, name=MORAUG, *, zone="battlefield", seat="A"):
        card = self.add(game.engine, name, zone=zone, seat=seat)
        self.assert_closed(game, card)
        return card

    def _accept(self, game, seat, payload):
        accepted = game.act("pilot:" + seat, payload)
        self.assertTrue(accepted.ok, accepted.summary)

    def _activate(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        ref = self._current(game, card).ref
        action, = (row["id"] for row in actions if row["id"].startswith(f"activate:{ref}:"))
        self._accept(game, seat, {"action_id": action, "pay": "auto"})

    def _play_land(self, game, land):
        self.priority(game, "A")
        self._accept(game, "A", {"action_id": "play-land:" + self._current(game, land).ref})

    def _find_land(self, game, wilds, basic):
        self._activate(game, "A", wilds)
        self.assertEqual("fetch_land", game.state.stack[-1].context["builtin"])
        self.assertIsNotNone(self.resolve(game))
        self._accept(game, "A", {"action_id": "choose", "search_card": self._current(game, basic).ref})
        self.assertEqual(("battlefield", "A"), (self._current(game, basic).zone, self._current(game, basic).controller))

    def _landfall(self, game, moraug):
        item, = [value for value in game.state.stack if value.source_object_id == moraug.object_id]
        self.assertEqual(("triggered_ability", "A"), (item.kind, item.controller))
        return item.ref

    def _delay(self, game, grant):
        from quorune.extra_combat_model import MAIN_PHASE_COMBAT_GRANT_FIELD

        delayed, = [entry for entry in game.state.delayed_triggers
                    if entry.condition.get(MAIN_PHASE_COMBAT_GRANT_FIELD) == grant]
        return delayed

    def _begin_granted(self, game, grant):
        delayed = self._delay(game, grant)
        self.until(game, lambda current: any(item.context.get("delayed_trigger_ref") == delayed.ref
                                             for item in current.state.stack))
        queue = game.state.main_phase_extra_combats
        self.assertEqual(("combat", "beginning_combat", grant),
                         (game.state.phase, game.state.step, queue.combat_grant))
        item, = [item for item in game.state.stack if item.context.get("delayed_trigger_ref") == delayed.ref]
        self.assertEqual("triggered_ability", item.kind)
        return game.state.combat.damage_sequence_id

    def _attackers(self, game, previous=None):
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers"
                   and (previous is None or current.state.combat.damage_sequence_id != previous))

    def test_actual_precombat_grants_precede_normal_combat_and_count_only_declarations(self):
        game = self.session(4822111)
        moraug = self._source(game)
        bear = self.add(game.engine, "Grizzly Bears", ref="declared-attack-bear")
        idle = self.add(game.engine, "Grizzly Bears", ref="late-copy-parent")
        wilds = self._source(game, "Evolving Wilds", zone="hand")
        forest = self.add(game.engine, "Forest", zone="library", ref="moraug-precombat-fetch")
        bear.tapped = True
        hidden = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="private-precombat-control")
        self.prepare(game)
        self.checkpoint(game)
        self._play_land(game, wilds)
        older = self._landfall(game, moraug)
        self.resolve_top(game)
        self._find_land(game, wilds, forest)
        newer = self._landfall(game, moraug)
        self.resolve_top(game)
        self.assertTrue(self._current(game, bear).tapped)
        queued = game.state.main_phase_extra_combats
        self.assertEqual(("precombat_main", [newer, older]),
                         (queued.after_phase, [entry.grant for entry in queued.pending]))
        game = self.replay(game, load=True)
        first = self._begin_granted(game, newer)
        self.assertTrue(self._current(game, bear).tapped)
        self.resolve_top(game)
        self.assertFalse(self._current(game, bear).tapped)
        self.attack(game, [(self._current(game, bear), "C")])
        self.assertEqual(3, self._power(game, bear))
        game = self.replay(game, load=True)
        late, = game.engine.create_token(
            "A", name="Creature put attacking", copy_of=self._current(game, idle).ref,
            tapped=True, attacking="B", reason="Committed creature entry after declared attackers",
        )
        entrant = next(card for card in game.state.cards.values() if card.ref == late)
        self.assertEqual((3, 2), (self._power(game, bear), self._power(game, entrant)))
        self.checkpoint(game)

        second = self._begin_granted(game, older)
        self.assertNotEqual(first, second)
        self.assertTrue(self._current(game, bear).tapped)
        self.resolve_top(game)
        self.attack(game, [(self._current(game, bear), "C")])
        self.assertEqual(4, self._power(game, bear))
        self._attackers(game, previous=second)
        self.assertIsNone(game.state.main_phase_extra_combats)
        self.assertTrue(self._current(game, bear).tapped)
        self.assertEqual(4, self._power(game, bear))
        self.assertFalse(any(item.context.get("delayed_trigger_ref") for item in game.state.stack))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_postcombat_grants_survive_departure_and_interleave_legacy_combat(self):
        game = self.session(4822112)
        moraug = self._source(game)
        bloodthirster = self._source(game, BLOOD)
        bear = self.add(game.engine, "Grizzly Bears")
        self._source(game, "Sphinx of the Second Sun")
        wilds = self._source(game, "Evolving Wilds", zone="hand")
        forest = self.add(game.engine, "Forest", zone="library", ref="moraug-postcombat-fetch")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        postcombat_event, = [
            event.event_id for event in game.state.events
            if event.code == "step.begin" and event.phase == "postcombat_main"
               and event.turn_sequence == game.state.turn_sequence
        ]
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.additional_beginning_phases.pending)
        self._play_land(game, wilds)
        older = self._landfall(game, moraug)
        self.resolve_top(game)
        self._find_land(game, wilds, forest)
        newer = self._landfall(game, moraug)
        self.cast_for(game, "B", bounce, [self._current(game, moraug).ref])
        self.resolve_top(game)
        self.assertEqual("hand", self._current(game, moraug).zone)
        self.resolve_top(game)
        self.assertEqual(["combat", "combat", "beginning"],
                         [entry.kind for entry in game.state.main_phase_extra_combats.pending])
        game = self.replay(game, load=True)

        first = self._begin_granted(game, newer)
        self.resolve_top(game)
        self.attack(game, [(self._current(game, bloodthirster), "B"), (self._current(game, bear), "D")])
        self.until(game, lambda current: self.trigger_count(current, bloodthirster) == 1)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self._attackers(game, previous=first)
        self.assertIsNone(game.state.main_phase_extra_combats.combat_grant)
        self.assertTrue(self._current(game, bear).tapped)
        self.assertTrue(self._delay(game, older).active)
        self.assertEqual(0, game.state.extra_combat_phases)
        legacy = game.state.combat.damage_sequence_id
        self.attack(game, [])

        last = self._begin_granted(game, older)
        self.assertNotEqual(last, legacy)
        self.assertTrue(self._current(game, bear).tapped)
        self.resolve_top(game)
        self.assertFalse(self._current(game, bear).tapped)
        self.attack(game, [])
        self.until(game, lambda current: current.state.phase == "beginning"
                   and current.state.additional_beginning_phases is not None
                   and current.state.additional_beginning_phases.active)
        self.until(game, lambda current: current.state.phase == "ending")
        self.assertIsNone(game.state.main_phase_extra_combats)
        self.assertIsNone(game.state.additional_beginning_phases)
        self.assertFalse(any(event.code == "step.begin" and event.phase == "postcombat_main"
                             and event.event_id > postcombat_event for event in game.state.events))
        self.replay(game, load=True)

    def test_actual_attack_history_stays_with_incarnation_through_control_then_resets_on_blink(self):
        game = self.session(4822113)
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="previous-controller-attacker")
        self._source(game, "Vedalken Orrery")
        moraug = self._source(game, zone="hand")
        borrow = self.add(game.engine, "Act of Treason", zone="hand")
        blink = self._source(game, "Cloudshift", zone="hand")
        for index in range(9):
            self.add(game.engine, "Mountain", ref=f"moraug-mountain-{index}")
        self.add(game.engine, "Plains", ref="moraug-blink-plains")
        self.prepare(game, actor="B", active="B")
        initial_identity = bear.logical_object_id
        self.checkpoint(game)
        self._attackers(game)
        self._accept(game, "B", {"action_id": "attack", "attackers": {bear.ref: "C"}})
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.cast_for(game, "A", borrow, [self._current(game, bear).ref], reserve={"W": 1})
        self.resolve_top(game)
        self.assertEqual(("A", initial_identity), (self._current(game, bear).controller, self._current(game, bear).logical_object_id))
        self.assertEqual(2, self._power(game, bear))
        self.cast_for(game, "A", moraug, reserve={"W": 1})
        self.resolve_top(game)
        self.assertEqual(3, self._power(game, bear))
        self.assertEqual(1, game.state.players["A"].mana_pool["W"])
        self.cast_for(game, "A", blink, [self._current(game, bear).ref])
        self.resolve_top(game)
        self.assertEqual("A", self._current(game, bear).controller)
        self.assertNotEqual(initial_identity, self._current(game, bear).logical_object_id)
        self.assertEqual(2, self._power(game, bear))
        self.replay(game, load=True)

    def test_actual_end_turn_discards_waiting_main_grants_and_foreign_turn_lands_do_not_trigger(self):
        game = self.session(4822114)
        moraug = self._source(game)
        wilds = self._source(game, "Evolving Wilds")
        forest = self.add(game.engine, "Forest", zone="library", ref="foreign-turn-fetch")
        self.prepare(game, actor="A", active="B")
        self.checkpoint(game)
        self._find_land(game, wilds, forest)
        self.assertEqual(0, self.trigger_count(game, moraug))
        self.assertIsNone(game.state.main_phase_extra_combats)
        self.replay(game, load=True)

        game = self.session(4822115)
        moraug = self._source(game)
        wilds = self._source(game, "Evolving Wilds", zone="hand")
        stop = self._source(game, "Sundial of the Infinite")
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self._play_land(game, wilds)
        grant = self._landfall(game, moraug)
        self.resolve_top(game)
        self.assertTrue(self._delay(game, grant).active)
        turn = game.state.turn_sequence
        self._activate(game, "A", stop)
        self.resolve_top(game)
        self.assertIsNone(game.state.main_phase_extra_combats)
        self.assertFalse(self._delay(game, grant).active)
        self.assertFalse(any(event.code == "step.begin" and event.step == "beginning_combat"
                             and event.turn_sequence == turn for event in game.state.events))
        self.replay(game, load=True)
