from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.mandatory_sacrifice_reanimation import MANDATORY_SACRIFICE_REANIMATION_CAPABILITY
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Victimize"
PROFILE = "commander_review"


class VictimizeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "victimize.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/victimize-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Mandatory sacrifice after target revalidation", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed, *, reply=None, may_pay=True, replacement=False):
        game = self.session(seed)
        host = game.engine
        spell = self.add(host, SOURCE, zone="hand", ref="victimize-spell")
        first = self.add(host, "Grizzly Bears", zone="graveyard", ref="victimize-first")
        second = self.add(host, "Darksteel Myr", zone="graveyard", ref="victimize-second")
        foreign = self.add(host, "Grizzly Bears", seat="B", zone="graveyard", ref="victimize-foreign")
        victim = self.add(host, "Grizzly Bears", seat="D", ref="victimize-victim") if may_pay else None
        if victim:
            host.change_control(victim.object_id, "A", reason="fixture separates owner and payer")
        other = self.add(host, "Grizzly Bears", seat="B", ref="victimize-other-player")
        artifact = self.add(host, "Sol Ring", ref="victimize-own-artifact")
        cremate = self.add(host, "Cremate", seat="C", zone="hand", ref="victimize-single-response") if reply == "one" else None
        crypt = self.add(host, "Tormod's Crypt", seat="D", ref="victimize-all-response") if reply == "both" else None
        if replacement:
            self.assert_closed(game, self.add(host, "Rest in Peace", seat="B", ref="victimize-replacement"))
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "B": 1})
        game.state.players["C"].mana_pool.update({"B": 1})
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        base = {"action_id": action["id"], "pay": "auto"}
        for bad in ([first.ref], [first.ref, first.ref], [first.ref, foreign.ref]):
            self.rejects(game, "A", {**base, "targets": bad})
        self.cast_for(game, "A", spell, [first.ref, second.ref])
        if victim:
            self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        if cremate:
            self.cast_for(game, "C", cremate, [first.ref])
            self.resolve_top(game)
        if crypt:
            self.priority(game, "D")
            matrix = game.packet("pilot:D", full=True)["decision"]["ctx"]["legal"]["actions"]
            offered, = [row for row in matrix if row["id"].startswith(f"activate:{crypt.ref}:")]
            result = game.act("pilot:D", {"action_id": offered["id"], "targets": ["A"]})
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(game)
        return game, first, second, victim, other, artifact

    def rejects(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_mandatory_payment_then_exact_remaining_creatures_enter_one_tapped_batch(self):
        for index, (reply, replacement) in enumerate(((None, False), ("one", False), (None, True))):
            with self.subTest(reply=reply, replacement=replacement):
                game, first, second, victim, other, artifact = self.start(7315300 + index,
                    reply=reply, replacement=replacement)
                decision = self.resolve(game)
                self.assertEqual(("semantic.choice", ("A",)), (decision.kind, tuple(decision.actors)))
                game = self.replay(game, load=True)
                self.rejects(game, "C", {"action_id": "choose", "cards": [victim.ref]})
                self.rejects(game, "A", {"action_id": "choose", "cards": [], "pay": False})
                self.rejects(game, "A", {"action_id": "choose", "cards": [other.ref]})
                self.rejects(game, "A", {"action_id": "choose", "cards": [artifact.ref]})
                with patch.object(game.engine, "move_objects_simultaneously_intent",
                                  wraps=game.engine.move_objects_simultaneously_intent) as commits:
                    result = game.act("pilot:A", {"action_id": "choose", "cards": [victim.ref]})
                self.assertTrue(result.ok, result.summary)
                moves = [call.args[0] for call in commits.call_args_list]
                sacrifice, entry = moves
                self.assertEqual((ZoneTransitionKind.SACRIFICE, (victim.ref,)),
                                 (sacrifice.transition_kind, sacrifice.object_refs))
                survivors = (second,) if reply else (first, second)
                self.assertCountEqual([card.ref for card in survivors], entry.object_refs)
                self.assertEqual(("battlefield", True), (entry.destination, entry.battlefield_tapped))
                self.assertEqual("exile" if replacement else "graveyard", game.state.cards[victim.object_id].zone)
                self.assertEqual("D", game.state.cards[victim.object_id].owner)
                for card in survivors:
                    current = game.state.cards[card.object_id]
                    self.assertEqual(("battlefield", "A", True), (current.zone, current.controller, current.tapped))
                if reply:
                    self.assertEqual("exile", game.state.cards[first.object_id].zone)
                self.replay(game, load=True)

    def test_illegal_pair_or_unavailable_payment_does_not_sacrifice_or_return(self):
        for index, reply in enumerate(("both", None)):
            with self.subTest(reply=reply):
                game, first, second, victim, _, _ = self.start(7315320 + index, reply=reply, may_pay=bool(reply))
                self.assertIsNone(self.resolve(game))
                zone = "exile" if reply else "graveyard"
                self.assertEqual((zone, zone), (game.state.cards[first.object_id].zone,
                                                game.state.cards[second.object_id].zone))
                if victim:
                    self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
                self.replay(game, load=True)

    def test_only_exact_two_owned_targets_and_the_independent_payment_owner_are_admitted(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(card.oracle_id)
        schema = {**program.target_schema, "count": 1}
        report = bind_semantic_program_runtime(replace(program, target_schema=schema),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"], report)
        deps = tuple(cap for cap in program.capability_dependencies if cap != MANDATORY_SACRIFICE_REANIMATION_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + MANDATORY_SACRIFICE_REANIMATION_CAPABILITY,
                      report["blockers"])
        with patch("quorune.rules.mandatory_sacrifice_reanimation.mandatory_sacrifice_reanimation_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)

    def test_amulet_self_entry_untaps_itself_only_if_it_entered_tapped(self):
        for tapped in (False, True):
            with self.subTest(tapped=tapped):
                game = self.session(7315351 + int(tapped))
                source = self.add(game.engine, "Amulet of Vigor", zone="hand")
                if tapped:
                    self.assert_closed(game, self.add(game.engine, "Orb of Dreams", seat="B"))
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 1})
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.resolve_top(game)
                entering = game.state.cards[source.object_id]
                self.assertEqual(tapped, entering.tapped)
                self.assertEqual(int(tapped), len(game.state.stack))
                if tapped:
                    trigger, = game.state.stack
                    self.assertEqual(source.object_id, trigger.source_object_id)
                    self.assertFalse(trigger.targets)
                    self.assertEqual(entering.logical_object_id, trigger.context["card_object_identity"])
                    game = self.replay(game, load=True)
                    self.assertIsNone(self.resolve(game))
                    self.assertFalse(game.state.cards[source.object_id].tapped)
                self.replay(game, load=True)

    def test_amulet_triggers_for_each_tapped_arrival_and_untaps_only_original_incarnation(self):
        game = self.session(7315353)
        amulet = self.add(game.engine, "Amulet of Vigor")
        spell = self.add(game.engine, "Victimize", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        icy = self.add(game.engine, "Icy Manipulator")
        first = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="amulet-first")
        other = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="amulet-other")
        victim = self.add(game.engine, "Grizzly Bears", ref="amulet-victim")
        for card in (amulet, spell, blink, icy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "B": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [first.ref, other.ref], reserve={"C": 1, "W": 1})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        paid = game.act("pilot:A", {"action_id": "choose", "cards": [victim.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        self.assertTrue(all(game.state.cards[card.object_id].tapped for card in (first, other)))
        old = game.state.cards[first.object_id].logical_object_id
        game = self.replay(game, load=True)
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in offered]})
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertCountEqual([first.ref, other.ref], [item.context["card"] for item in game.state.stack])
        self.assertTrue(all(not item.targets for item in game.state.stack))
        self.cast(game, "A", blink, [first.ref])
        self.resolve_top(game)
        changed = game.state.cards[first.object_id]
        self.assertNotEqual(old, changed.logical_object_id)
        self.assertFalse(changed.tapped)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{icy.ref}:")]
        tapped = game.act("pilot:A", {"action_id": action["id"], "targets": [first.ref], "pay": "auto"})
        self.assertTrue(tapped.ok, tapped.summary)
        self.resolve_top(game)
        self.assertTrue(game.state.cards[first.object_id].tapped)
        self.assertEqual(2, len(game.state.stack))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[first.object_id].tapped)
        self.assertFalse(game.state.cards[other.object_id].tapped)
        self.replay(game, load=True)


class TappedEntryUntapCompilerTests(unittest.TestCase):
    def test_tapped_entry_trigger_and_untap_require_the_original_controller_and_occurrence(self):
        from quorune.rules.entry_permanent_results import (
            entry_permanent_result_binding, tapped_entry_untap_condition,
        )
        from test_qualified_zone_event_queries import query_record

        text = "Whenever a permanent you control enters tapped, untap it."

        def compile_text(source):
            return compile_oracle_card(replace(query_record(source), type_line="Artifact"),
                                       capability_registry=load_default_capability_registry())

        def witness():
            ir = compile_text(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual("permanent.enter", node.event)
            self.assertEqual(tapped_entry_untap_condition(), node.event_condition)
            self.assertEqual({"op": "untap", "card": "$event.entry_object"}, node.effects[0])
            self.assertIsNone(node.target_schema)

        witness()
        malformed = tapped_entry_untap_condition()
        malformed["all"][1]["value"] = 1
        self.assertFalse(entry_permanent_result_binding("permanent.enter", malformed, "untap"))
        with patch("quorune.compiler.tap_state_event_bindings.tapped_entry_untap_binding", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for altered in (
            text.replace("you control", "an opponent controls"),
            text.replace("enters tapped", "becomes tapped"),
            text.replace("untap it", "untap another permanent"),
        ):
            ir = compile_text(altered)
            self.assertTrue(all(node.event != "permanent.enter" or not node.exact for node in ir.faces[0].nodes))


class DarettiArtifactResolutionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os

        cls.temporary = TemporaryDirectory()
        database = os.environ.get("MTG_CARD_DB")
        if database is None:
            database = Path(cls.temporary.name) / "daretti.sqlite3"
            fixtures = ROOT / "tests" / "fixtures"
            build_fixture_database([fixtures / name for name in (
                "victimize-cards.json", "fixed-subset-discard-draw-cards.json",
                "target-artifact-destruction-treasure-cards.json", "event-player-fixed-damage-cards.json",
                "face-up-exiled-card-owner-shuffle-cards.json", "creature-subtype-set-results-cards.json",
                "mutate-casting-cards.json", "source-attack-alone-cards.json",
            )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Mandatory artifact sacrifice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed, *, mode):
        from quorune.rules.mandatory_sacrifice_reanimation import MANDATORY_ARTIFACT_REANIMATION_VERSION

        game = self.session(seed)
        host = game.engine
        source = self.add(host, "Daretti, Scrap Savant")
        source.counters["loyalty"] = 4
        target = self.add(host, "Sol Ring", zone="graveyard", ref="daretti-own-target")
        foreign = self.add(host, "Sol Ring", seat="B", zone="graveyard", ref="daretti-foreign-target")
        uncontrolled = self.add(host, "Sol Ring", seat="B", ref="daretti-other-sacrifice")
        control = None
        if mode != "no-offer":
            control = self.add(host, "Darksteel Myr", seat="D", ref="daretti-borrowed-artifact")
            host.change_control(control.object_id, "A", reason="Fixture uses control independently of ownership")
        removal = None
        if mode == "target-lost":
            removal = self.add(host, "Cremate", seat="C", zone="hand")
            self.assert_closed(game, removal)
        if mode == "replace-cost":
            self.assert_closed(game, self.add(host, "Rest in Peace", seat="B"))
        program = host.semantics.get(source.oracle_id + ":ability:ab2")
        self.assertEqual(MANDATORY_ARTIFACT_REANIMATION_VERSION, program.effects[0]["payment"]["schema_version"])
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
                                                      profile=PROFILE)["strict"])
        self.prepare(game)
        game.state.players["C"].mana_pool["B"] = 1
        self.checkpoint(game)
        action = next(row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"] == f"activate:{source.ref}:ab2")
        command = {"action_id": action, "targets": [target.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        invalid = game.act("pilot:A", {**command, "targets": [foreign.ref]})
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, game.state.cards[source.object_id].counters["loyalty"])
        if control:
            self.assertEqual("battlefield", game.state.cards[control.object_id].zone)
        if removal:
            self.cast(game, "C", removal, [target.ref])
            self.resolve_top(game)
        return game, target, control, uncontrolled

    def test_actual_artifact_sacrifice_is_mandatory_on_resolution_and_uses_control(self):
        for index, mode in enumerate(("normal", "replace-cost")):
            with self.subTest(mode=mode):
                game, target, control, uncontrolled = self.start(7315320 + index, mode=mode)
                decision = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
                offer = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
                self.assertEqual([control.ref], offer["legal_refs"])
                game = self.replay(game, load=True)
                for seat, selection in (("A", []), ("A", [uncontrolled.ref]), ("B", [control.ref])):
                    before = authoritative_state_hash(game.state)
                    rejected = game.act(f"pilot:{seat}", {"action_id": "choose", "cards": selection})
                    self.assertFalse(rejected.ok, rejected.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", {"action_id": "choose", "cards": [control.ref]})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(game))
                arrived = game.state.cards[target.object_id]
                self.assertEqual(("battlefield", "A", False), (arrived.zone, arrived.controller, arrived.tapped))
                self.assertEqual("exile" if mode == "replace-cost" else "graveyard",
                                 game.state.cards[control.object_id].zone)
                self.assertEqual("D", game.state.cards[control.object_id].owner)
                self.assertEqual("battlefield", game.state.cards[uncontrolled.object_id].zone)
                self.replay(game, load=True)

    def test_actual_emblem_seals_each_owned_artifact_departure_and_only_returns_its_incarnation(self):
        game = self.session(7315324)
        host = game.engine
        source = self.add(host, "Daretti, Scrap Savant")
        source.counters["loyalty"] = 10
        departed = self.add(host, "Grizzly Bears", ref="daretti-type-changed-artifact")
        host.change_control(departed.object_id, "B", reason="Fixture isolates graveyard ownership")
        stale = self.add(host, "Sol Ring", ref="daretti-stale-arrival")
        coating = self.add(host, "Liquimetal Coating", seat="B")
        shatter = self.add(host, "Shatterstorm", zone="hand")
        cremate = self.add(host, "Cremate", seat="C", zone="hand")
        pull = self.add(host, "Pull from Eternity", zone="hand")
        plains = self.add(host, "Plains", ref="daretti-reserved-plain")
        token_ref, = host.create_token("A", name="Treasure Token", reason="Fixture artifact token",
                                       characteristics={"type_line": "Token Artifact — Treasure"})
        for card in (source, coating, shatter, cremate, pull):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 2})
        game.state.players["C"].mana_pool["B"] = 1
        self.checkpoint(game)
        self.assertTrue(game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab3"}).ok)
        self.assertIsNone(self.resolve(game))
        emblem, = [card for card in game.state.cards.values() if card.object_kind == "emblem"]
        self.assertEqual("A", emblem.controller)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [action["id"] for action in actions if action["id"].startswith(f"activate:{coating.ref}:")]
        self.assertTrue(game.act("pilot:B", {"action_id": offer, "targets": [departed.ref]}).ok)
        self.assertIsNone(self.resolve(game))
        self.assertIn("artifact", game.engine._effective_type_parts(game.state.cards[departed.object_id])[0])
        self.cast(game, "A", shatter)
        self.assertFalse(game.state.cards[plains.object_id].tapped)
        self.resolve_top(game)
        decision = game.state.pending_decision
        self.assertEqual(("trigger.order", ["A"]), (decision.kind, decision.actors))
        pending = [item for batch in game.state.pending_trigger_batches for item in batch.items]
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": [item.ref for item in pending]})
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual({departed.ref, stale.ref, token_ref}, {item.context["card"] for item in game.state.stack})
        self.assertTrue(all(item.source_object_id == emblem.object_id for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(game.state.delayed_triggers))
        original_identity = game.state.cards[stale.object_id].logical_object_id
        game = self.replay(game, load=True)
        self.cast(game, "C", cremate, [stale.ref])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", pull, [stale.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original_identity, game.state.cards[stale.object_id].logical_object_id)
        self.assertEqual(2, len(game.state.delayed_triggers))
        self.until(game, lambda current: current.state.step == "end_step" and len(current.state.stack) == 2)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[departed.object_id]
        self.assertEqual(("battlefield", "A", "Grizzly Bears"), (returned.zone, returned.controller, returned.printed_name))
        self.assertNotIn("artifact", game.engine._effective_type_parts(returned)[0])
        self.assertEqual("graveyard", game.state.cards[stale.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[coating.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_merged_artifact_returns_only_current_components_and_keeps_old_event_shape(self):
        import test_mutate_cast_native as mutate_native

        from quorune.delayed_object_model import ARTIFACT_GRAVEYARD_COMPOSITION_FIELD
        from quorune.replacement.immutable import FrozenMap, thaw_value
        from quorune.util import stable_json
        from quorune.zone_trigger_events import normalized_zone_trigger_events

        game = self.session(7315327)
        host = game.engine
        source = self.add(host, "Daretti, Scrap Savant")
        source.counters["loyalty"] = 10
        base = self.add(host, "Ornithopter", ref="daretti-merged-artifact")
        stale = self.add(host, "Dreamtail Heron", zone="hand", ref="daretti-stale-component")
        survivor = self.add(host, "Dreamtail Heron", zone="hand", ref="daretti-current-component")
        observer = self.add(host, "Viridian Revel", seat="D")
        destroy = self.add(host, "Disenchant", seat="B", zone="hand")
        cremate = self.add(host, "Cremate", seat="C", zone="hand")
        pull = self.add(host, "Pull from Eternity", zone="hand")
        self.add(host, "Plains", ref="daretti-group-reserved-plain")
        for card in (source, base, stale, observer, destroy, cremate, pull):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        game.state.players["C"].mana_pool["B"] = 1
        self.checkpoint(game)
        self.assertTrue(game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab3"}).ok)
        self.assertIsNone(self.resolve(game))
        emblem, = [card for card in game.state.cards.values() if card.object_kind == "emblem"]
        for part in (stale, survivor):
            mutate_native.MutateCastNativeTests.mutate(self, game, part, base)
            game = mutate_native.MutateCastNativeTests.choose_merge(self, game, "bottom")
            self.settle(game)
        self.cast(game, "B", destroy, [base.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("composition_departure_order", game.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])
        with patch("quorune.zone_trigger_processing.normalized_zone_trigger_events",
                   wraps=normalized_zone_trigger_events) as normalize:
            result = game.act("pilot:A", {"action_id": "choose", "cards": [base.ref, stale.ref, survivor.ref]})
        self.assertTrue(result.ok, result.summary)
        normalized = [(call.args[0], thaw_value(row.context)) for call in normalize.call_args_list
                      for row in normalized_zone_trigger_events(call.args[0])
                      if row.kind == "artifact.graveyard" and row.context["card"] == base.ref]
        self.assertTrue(normalized)
        occurrence, original = normalized[0]
        self.assertTrue(all(current == occurrence and stable_json(value) == stable_json(original)
                            for current, value in normalized))
        scheduled, = [item for item in game.state.stack if item.source_object_id == emblem.object_id]
        unrelated, = [item for item in game.state.stack if item.source_object_id == observer.object_id]
        self.assertNotIn(ARTIFACT_GRAVEYARD_COMPOSITION_FIELD, unrelated.context)
        self.assertEqual(stable_json(original), stable_json(thaw_value(FrozenMap(
            {field: unrelated.context[field] for field in original}))))
        self.assertEqual({base.object_id, stale.object_id, survivor.object_id},
                         {row["object_id"] for row in scheduled.context[ARTIFACT_GRAVEYARD_COMPOSITION_FIELD]})
        offer = self.resolve(game)
        self.assertEqual(("semantic.choice", ["D"]), (offer.kind, offer.actors))
        accepted = game.act("pilot:D", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        delayed, = [trigger for trigger in game.state.delayed_triggers if trigger.active]
        self.assertEqual({base.object_id, stale.object_id, survivor.object_id}, set(delayed.referred_object_ids))
        stale_arrival = game.state.cards[stale.object_id].logical_object_id
        game = self.replay(game, load=True)
        self.cast(game, "C", cremate, [stale.ref])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", pull, [stale.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(stale_arrival, game.state.cards[stale.object_id].logical_object_id)
        self.until(game, lambda current: current.state.step == "end_step" and len(current.state.stack) == 1)
        self.assertIsNone(self.resolve(game))
        first, second = (game.state.cards[card.object_id] for card in (base, survivor))
        self.assertEqual(("battlefield", "battlefield"), (first.zone, second.zone))
        self.assertEqual(first.zone_timestamp, second.zone_timestamp)
        self.assertEqual(("A", "A"), (first.controller, second.controller))
        self.assertEqual("graveyard", game.state.cards[stale.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_plus_two_and_printed_commander_eligibility_are_retained(self):
        from quorune.deck import DeckLoader

        for seed, count in ((7315325, 0), (7315326, 2)):
            with self.subTest(count=count):
                game = self.session(seed)
                source = self.add(game.engine, "Daretti, Scrap Savant")
                source.counters["loyalty"] = 3
                self.assert_closed(game, source)
                deck = DeckDefinition("Planeswalker commander", [
                    DeckEntry(source.printed_name, 1, "commander"), DeckEntry("Plains", 30),
                ], [source.printed_name])
                self.assertEqual([], DeckLoader(self.db).validate_commander_deck(
                    deck, expected_size=31, check_color_identity=False,
                ))
                self.prepare(game)
                self.checkpoint(game)
                hand = game.state.players["A"].zones["hand"]
                selected = [game.state.cards[object_id].ref for object_id in hand[:count]]
                library_size = len(game.state.players["A"].zones["library"])
                result = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1"})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                result = game.act("pilot:A", {"action_id": "choose", "cards": selected})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(5, game.state.cards[source.object_id].counters["loyalty"])
                self.assertEqual(library_size - count, len(game.state.players["A"].zones["library"]))
                self.replay(game, load=True)

    def test_merged_artifact_without_a_constituent_receipt_fails_closed(self):
        from types import SimpleNamespace

        from quorune.delayed_object_actions import bind_delayed_object_instruction
        from quorune.delayed_object_model import artifact_graveyard_return_effect, validate_delayed_object_instruction

        source = SimpleNamespace(object_kind="emblem", zone="command", logical_object_id="E@0", controller="A")
        host = SimpleNamespace(state=SimpleNamespace(cards={"E": source}))
        facts = {
            "source_logical_object_id": "E@0", "event": "artifact.graveyard", "owner": "A",
            "from": "battlefield", "to": "graveyard", "types": ["artifact"], "token": False,
            "card_object_identity": "physical@2", "card_zone_change_counter": 3, "card": "ART",
        }
        item = SimpleNamespace(source_object_id="E", context=facts)
        result = bind_delayed_object_instruction(host, artifact_graveyard_return_effect(), item)
        self.assertEqual([{"object_id": "physical", "reference": "ART", "zone_change_counter": 3, "owner": "A"}],
                         result["object"])
        with self.assertRaises(ValueError):
            validate_delayed_object_instruction({key: value for key, value in result.items() if key != "object"})
        item.context = {**facts, "composition": {"member_count": 2}}
        with self.assertRaisesRegex(ValueError, "sealed arrivals"):
            bind_delayed_object_instruction(host, artifact_graveyard_return_effect(), item)

    def test_actual_lost_target_or_unavailable_sacrifice_leaves_the_other_object(self):
        for index, mode in enumerate(("target-lost", "no-offer")):
            with self.subTest(mode=mode):
                game, target, control, uncontrolled = self.start(7315322 + index, mode=mode)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile" if mode == "target-lost" else "graveyard",
                                 game.state.cards[target.object_id].zone)
                if control:
                    self.assertEqual("battlefield", game.state.cards[control.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[uncontrolled.object_id].zone)
                self.replay(game, load=True)
