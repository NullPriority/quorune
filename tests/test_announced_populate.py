"""An announced Populate count is a sequence of real copy choices."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class AnnouncedPopulateTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-populate.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/announced-populate-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced Populate", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def tokens(self, game, seat):
        return {card.ref: card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.controller == seat}

    def token_subtypes(self, game, token):
        data = game.engine._effective_card_data(token)
        return game.engine._type_parts(data["type_line"])[1]

    def cast_announced(self, game, spell, x):
        self.priority(game, "A")
        offered = self.action(game, "A", spell)
        mana_before = sum(game.state.players["A"].mana_pool.values())
        result = game.act("pilot:A", {"action_id": offered["id"], "x": x, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)
        self.assertEqual(2 * x + 1, mana_before - sum(game.state.players["A"].mana_pool.values()))
        return game.state.stack[-1].ref

    def choices_for_current_populate(self, game, stack_ref):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual([stack_ref], [item.ref for item in game.state.stack])
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        return set(ctx["legal_actions"][0]["choice_schema"]["legal_refs"])

    def test_actual_full_flowering_repeats_using_current_tokens_and_held_triggers(self):
        record = self.db.lookup("Full Flowering", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Full Flowering")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        forged = replace(program, effects=[{**program.effects[0], "count": 3}])
        self.assertFalse(bind_semantic_program_runtime(
            forged, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.fixed_token_production.announced_populate_effect",
                   return_value={"op": "populate", "schema_version": 2, "count": 3}):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

        game = self.playing(61127257)
        flower = self.add(game.engine, "Full Flowering", zone="hand")
        soldiers = self.add(game.engine, "Raise the Alarm", zone="hand")
        rival = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand", ref="opponent-token-maker")
        goblins = self.add(game.engine, "Dragon Fodder", zone="hand")
        warden = self.add(game.engine, "Soul Warden", zone="hand")
        for card in (flower, soldiers, rival, goblins, warden):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 3, "W": 2, "R": 1, "G": 7})
        game.state.players["B"].mana_pool["W"] = 2
        self.checkpoint(game)
        for seat, spell in (("A", soldiers), ("B", rival), ("A", goblins), ("A", warden)):
            self.cast_for(game, seat, spell)
            self.assertIsNone(self.resolve(game))

        prior = self.tokens(game, "A")
        soldier = next(ref for ref, token in prior.items() if "soldier" in self.token_subtypes(game, token))
        goblin = next(ref for ref, token in prior.items() if "goblin" in self.token_subtypes(game, token))
        foreign = next(iter(self.tokens(game, "B")))
        start_life = game.state.players["A"].life
        stack_ref = self.cast_announced(game, flower, 3)
        self.assertEqual(set(prior), self.choices_for_current_populate(game, stack_ref))
        before = authoritative_state_hash(game.state)
        for seat, selected in (("B", soldier), ("A", foreign)):
            self.assertFalse(game.act("pilot:" + seat, {"action_id": "choose", "card": selected}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))

        first = game.act("pilot:A", {"action_id": "choose", "card": soldier})
        self.assertTrue(first.ok, first.summary)
        new_soldier, = set(self.tokens(game, "A")) - set(prior)
        self.assertIn(new_soldier, self.choices_for_current_populate(game, stack_ref))
        self.assertEqual(start_life, game.state.players["A"].life)
        game = self.replay(game, load=True)
        second = game.act("pilot:A", {"action_id": "choose", "card": goblin})
        self.assertTrue(second.ok, second.summary)
        self.assertIn(new_soldier, self.choices_for_current_populate(game, stack_ref))
        self.assertEqual(start_life, game.state.players["A"].life)
        third = game.act("pilot:A", {"action_id": "choose", "card": new_soldier})
        self.assertTrue(third.ok, third.summary)

        results = [self.token_subtypes(game, token) for token in self.tokens(game, "A").values()]
        self.assertEqual(7, len(results))
        self.assertEqual((3, 4), tuple(sum(kind in found for found in results) for kind in ("goblin", "soldier")))
        self.assertEqual("graveyard", game.state.cards[flower.object_id].zone)
        pending = game.state.pending_decision
        if pending.kind == "trigger.order":
            self.assertEqual(["A"], pending.actors)
            group, = game.state.pending_trigger_batches[0].groups
            self.assertEqual(3, len(group.items))
            ordered = game.act("pilot:A", {"action_id": "order", "order": [item.ref for item in group.items]})
            self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual(["triggered_ability"] * 3, [item.kind for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(start_life + 3, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_zero_or_no_eligible_creature_tokens_never_issues_a_choice(self):
        for x, with_tokens in ((0, True), (2, False)):
            with self.subTest(x=x, with_tokens=with_tokens):
                game = self.playing(61127258 + x)
                flower = self.add(game.engine, "Full Flowering", zone="hand")
                soldiers = self.add(game.engine, "Raise the Alarm", zone="hand")
                self.assert_closed(game, flower)
                self.prepare(game, actor="A", active="A", mana={"C": 1, "W": 1, "G": 5})
                self.checkpoint(game)
                if with_tokens:
                    self.assert_closed(game, soldiers)
                    self.cast_for(game, "A", soldiers)
                    self.assertIsNone(self.resolve(game))
                initial = set(self.tokens(game, "A"))
                self.assertEqual(with_tokens, bool(initial))
                self.cast_announced(game, flower, x)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(initial, set(self.tokens(game, "A")))
                self.assertEqual("graveyard", game.state.cards[flower.object_id].zone)
                self.replay(game, load=True)


class SourceBoundPopulateTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json

        cls.temporary = TemporaryDirectory()
        directory = Path(cls.temporary.name)
        fixtures = {
            "scryfall-exact-lists.json": {"Determined Iteration", "Sundial of the Infinite"},
            "announced-populate-cards.json": {"Isamaru, Hound of Konda", "Plains", "Raise the Alarm"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Grizzly Bears", "Disenchant"},
            "combat-damage-dealer-recipient-control-cards.json": {"Word of Seizing", "Mountain"},
            "fixed-distributed-token-cards.json": {"Anointed Procession"},
            "additional-token-definition-cards.json": {"Parallel Lives"},
            "entry-equipment-attachment-cards.json": {"Invisible Stalker"},
        }
        selected = []
        for name, wanted in fixtures.items():
            source = json.loads((ROOT / "tests" / "fixtures" / name).read_bytes())
            source["cards"] = [row for row in source["cards"] if row["name"] in wanted]
            assert {row["name"] for row in source["cards"]} == wanted
            ids = {row["oracle_id"] for row in source["cards"]}
            source["rulings"] = [row for row in source.get("rulings", ()) if row["oracle_id"] in ids]
            fragment = directory / name
            fragment.write_text(json.dumps(source), encoding="utf-8")
            selected.append(fragment)
        path = directory / "source-bound-populate.sqlite3"
        build_fixture_database(selected, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source-bound Populate", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, card, *, active="A"):
        self.assert_closed(game, card)
        trigger, = [program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "step.begin"]
        self.assertEqual(({"op": "populate", "schema_version": 3},), tuple(trigger.effects))
        self.assertIsNone(trigger.target_schema)
        game.state.config.semantic_policy = "trusted_only"
        for object_id in game.state.players["A"].zones["hand"][:3]:
            game.engine.move_card(object_id, "library", log=False)
        self.prepare(game, actor=active, active=active)
        self.checkpoint(game)
        return trigger

    def active_delays(self, game):
        return [row for row in game.state.delayed_triggers if row.active]

    def tokens(self, game):
        return {card.ref: card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"}

    def native_token(self, game, seat, model):
        ref, = game.engine.create_token(seat, name="", copy_of=model.ref)
        return self.tokens(game)[ref]

    def own_combat_trigger(self, game, iteration):
        self.until(game, lambda match: any(item.source_object_id == iteration.object_id for item in match.state.stack))
        item, = game.state.stack
        self.assertEqual(("beginning_combat", "A", []), (game.state.step, item.controller, item.targets))
        self.assertEqual(iteration.logical_object_id, item.context["source_logical_object_id"])
        return item

    def act_ok(self, game, seat, command):
        outcome = game.act("pilot:" + seat, command)
        self.assertTrue(outcome.ok, outcome.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def options(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        return set(ctx["legal_actions"][0]["choice_schema"]["legal_refs"])

    def test_actual_populate_replacements_left_source_and_first_actual_end_step(self):
        from quorune.haste import has_effective_haste

        game = self.session(70136003)
        iteration = self.add(game.engine, "Determined Iteration")
        bear = self.add(game.engine, "Grizzly Bears")
        stalker = self.add(game.engine, "Invisible Stalker")
        model = self.native_token(game, "A", bear)
        hexproof_model = self.native_token(game, "A", stalker)
        other_model = self.native_token(game, "B", bear)
        model.counters["+1/+1"] = 1
        model.tapped = True
        treasure_ref, = game.engine.create_token("A", name="Test Treasure", characteristics={"type_line": "Artifact"})
        self.add(game.engine, "Anointed Procession")
        self.add(game.engine, "Parallel Lives")
        sundial = self.add(game.engine, "Sundial of the Infinite")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        theft = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        self.add(game.engine, "Plains", ref="end-turn-mana")
        for index in range(3):
            self.add(game.engine, "Plains", seat="B", ref=f"response-plains-{index}")
        for index in range(2):
            self.add(game.engine, "Mountain", seat="B", ref=f"response-mountain-{index}")
        self.start(game, iteration)
        firing_identity = self.own_combat_trigger(game, iteration).context["source_logical_object_id"]
        self.cast(game, "B", removal, [iteration.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[iteration.object_id].zone)
        self.assertEqual({model.ref, hexproof_model.ref}, self.options(game))
        for seat, command in (
            ("B", {"card": model.ref}), ("A", {"card": other_model.ref}),
            ("A", {"card": treasure_ref}), ("A", {}),
        ):
            self.reject(game, seat, {"action_id": "choose", **command})
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        game = self.replay(game, load=True)
        prior = set(self.tokens(game))
        self.act_ok(game, "A", {"action_id": "choose", "card": model.ref})
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(prior, set(self.tokens(game)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "C", command)
        self.act_ok(game, "A", command)
        self.settle(game)
        created = {ref: card for ref, card in self.tokens(game).items() if ref not in prior}
        self.assertEqual(4, len(created))
        for token in created.values():
            self.assertTrue(has_effective_haste(game.engine, token))
            self.assertNotIn("Haste", game.engine._copyable_characteristics(token)["keywords"])
            self.assertEqual((False, {}), (token.tapped, token.counters))
        cleanup, = self.active_delays(game)
        self.assertEqual(("A", firing_identity, {token.object_id for token in created.values()}),
                         (cleanup.controller, cleanup.source_logical_object_id, set(cleanup.referred_object_ids)))
        self.assertEqual({"phase": "ending", "step": "end_step"}, cleanup.condition)
        source_binding = cleanup.stack_template["context"]["targeting_source_characteristics"]
        self.assertEqual(("battlefield", firing_identity), (source_binding["zone"], source_binding["logical_object_id"]))
        game = self.replay(game, load=True)
        stolen = next(iter(created.values()))
        self.attack(game, [(stolen, "B")])
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith("activate:" + sundial.ref + ":")]
        self.act_ok(game, "A", {"action_id": offer["id"], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual("B", game.state.active_player)
        self.assertEqual([cleanup.ref], [trigger.ref for trigger in self.active_delays(game)])
        self.assertTrue(all(has_effective_haste(game.engine, game.state.cards[token.object_id]) for token in created.values()))
        self.cast(game, "B", theft, [stolen.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[stolen.object_id].controller)
        self.until(game, lambda match: match.state.active_player == "B" and match.state.step == "end_step")
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.active_delays(game))
        self.assertEqual(prior | {stolen.ref}, set(self.tokens(game)))
        self.assertEqual("B", game.state.cards[stolen.object_id].controller)
        self.assertTrue(has_effective_haste(game.engine, game.state.cards[stolen.object_id]))
        self.replay(game, load=True)

    def test_actual_empty_populate_uses_only_the_correct_combat_and_can_find_a_late_token(self):
        for active, add_late in (("B", False), ("A", False), ("A", True)):
            with self.subTest(active=active, add_late=add_late):
                game = self.session(70136202 + int(add_late))
                iteration = self.add(game.engine, "Determined Iteration")
                if add_late:
                    instant = self.add(game.engine, "Raise the Alarm", zone="hand")
                    for index in range(2):
                        self.add(game.engine, "Plains", ref=f"late-token-mana-{index}")
                self.start(game, iteration, active=active)
                if active == "B":
                    self.until(game, lambda match: match.state.step == "beginning_combat")
                    self.assertFalse(game.state.stack)
                else:
                    self.own_combat_trigger(game, iteration)
                    if add_late:
                        self.assertFalse(self.tokens(game))
                        self.cast(game, "A", instant)
                        self.resolve_top(game)
                        candidates = self.options(game)
                        self.assertEqual(2, len(candidates))
                        self.act_ok(game, "A", {"action_id": "choose", "card": min(candidates)})
                        self.assertEqual(3, len(self.tokens(game)))
                        self.assertEqual(1, len(self.active_delays(game)))
                    else:
                        self.assertIsNone(self.resolve(game))
                        self.assertFalse(self.tokens(game))
                        self.assertFalse(self.active_delays(game))
                self.replay(game, load=True)

    def test_strict_populate_does_not_supersede_a_changed_legacy_result(self):
        from quorune.card_programs.trigger_view_equivalence import shadowed_reviewed_trigger_view_keys
        from quorune.semantics import SemanticRegistry

        game = self.session(70136004)
        card = self.add(game.engine, "Determined Iteration")
        current = self.start(game, card)
        printed = self.db.lookup(card.printed_name, fuzzy=False)
        old, = [program for program in SemanticRegistry().programs_for_oracle(printed.oracle_id)
                if program.event == "step.begin"]
        self.assertEqual({"op": "populate_with_haste", "player": "$controller"}, old.effects[0])
        self.assertEqual({old.key}, shadowed_reviewed_trigger_view_keys(
            printed, (current,), (old,), capability_registry=self.registry, capability_profile="commander_review",
        ))
        changed = replace(old, effects=({**old.effects[0], "player": "$opponent"},))
        self.assertFalse(shadowed_reviewed_trigger_view_keys(
            printed, (current,), (changed,), capability_registry=self.registry, capability_profile="commander_review",
        ))
        scope = {"capability_registry": self.registry, "profile": "commander_review"}
        for malformed in (
            replace(current, effects=({**current.effects[0], "count": 2},)),
            replace(current, effects=({"op": "offer_optional_effect", "player": "$controller",
                                       "effects": list(current.effects)},)),
            replace(current, target_schema={"zones": ["battlefield"], "categories": ["permanent"], "count": 1}),
        ):
            self.assertFalse(bind_semantic_program_runtime(malformed, **scope)["strict"])
