"""Choose zero through all actual eligible permanents during resolution."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.any_number_sacrifice import ANY_NUMBER_SACRIFICE_CAPABILITY, any_number_sacrifice_parts
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class AnyNumberSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "any-number-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/any-number-sacrifice-resolution-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Any-number sacrifice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def choice(self, game, options):
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, decision.actors))
        self.assertFalse(game.state.stack[-1].targets)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(set(options), set(context["options"]))
        self.assertEqual((0, len(options)), (context["minimum"], context["count"]))

    def reject(self, game, seat, refs):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, {"action_id": "choose", "cards": list(refs)}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose(self, game, refs):
        result = game.act("pilot:A", {"action_id": "choose", "cards": list(refs)})
        self.assertTrue(result.ok, result.summary)

    def actual_draws(self, game):
        return len(game.state.players["A"].draw_history)

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_actual_reprocess_accepts_a_strict_subset_and_draws_before_the_death_trigger(self):
        game = self.session(76271101)
        spell = self.add(game.engine, "Reprocess", zone="hand")
        relic = self.add(game.engine, "Darksteel Relic")
        traveler = self.add(game.engine, "Doomed Traveler")
        forest = self.add(game.engine, "Forest")
        enchantment = self.add(game.engine, "Omen of the Sea")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B")
        drawn = tuple(game.state.players["A"].zones["library"][-2:])
        initial = self.actual_draws(game)
        self.start(game, spell, mana={"C": 2, "B": 2})
        self.cast(game, "A", spell)
        self.choice(game, (relic.ref, traveler.ref, forest.ref))
        self.reject(game, "B", (foreign.ref,))
        self.reject(game, "A", (enchantment.ref,))
        self.reject(game, "A", (traveler.ref, traveler.ref))
        self.assertEqual("battlefield", self.zone(game, traveler))
        game = self.replay(game, load=True)
        self.choose(game, (relic.ref, traveler.ref))
        self.assertEqual(initial + 2, self.actual_draws(game))
        for object_id in drawn:
            self.assertIn(object_id, game.state.players["A"].zones["hand"])
            reference = game.state.cards[object_id].ref
            for seat in "BCD":
                self.assertNotIn(reference, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.assertEqual(("graveyard", "graveyard", "battlefield", "battlefield"),
                         tuple(self.zone(game, card) for card in (relic, traveler, forest, enchantment)))
        self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
        self.assertEqual(1, self.trigger_count(game, traveler))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, sum(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_actual_renounce_uses_zero_or_two_life_per_real_redirected_sacrifice(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                game = self.session(76271110 + selected)
                spell = self.add(game.engine, "Renounce", zone="hand")
                redirect = self.add(game.engine, "Rest in Peace", seat="D")
                leyline = self.add(game.engine, "Leyline of the Void", seat="D")
                own = self.add(game.engine, "Omen of the Sea")
                forest = self.add(game.engine, "Forest")
                life = game.state.players["A"].life
                self.start(game, spell, redirect, leyline, mana={"C": 1, "W": 1})
                self.cast(game, "A", spell)
                self.choice(game, (own.ref, forest.ref))
                self.assertEqual(life, game.state.players["A"].life)
                self.choose(game, (own.ref, forest.ref) if selected else ())
                if selected:
                    for _ in range(2):
                        pending = self.resolve(game)
                        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
                        self.assertEqual(life, game.state.players["A"].life)
                        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, own), self.zone(game, forest)))
                        game = self.replay(game, load=True)
                        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                        self.assertEqual(2, len(options))
                        result = game.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
                        self.assertTrue(result.ok, result.summary)
                pending = self.resolve(game)
                self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
                self.assertEqual(life + 4 * int(selected), game.state.players["A"].life)
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                result = game.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", self.zone(game, spell))
                expected = "exile" if selected else "battlefield"
                self.assertEqual((expected, expected), (self.zone(game, own), self.zone(game, forest)))
                self.replay(game, load=True)

    def test_bontu_owner_pronouns_preserve_the_source_departure_effect(self):
        card = self.db.lookup("God-Eternal Bontu", fuzzy=False)

        def compile_text(text):
            return compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=PROFILE,
            )

        old = compile_text(card.oracle_text)
        self.assertEqual("exact", old.status)
        baseline, = (node for node in old.faces[0].nodes if node.event == "permanent.leave")
        for subject, possessor in (("him", "his"), ("her", "her")):
            with self.subTest(subject=subject):
                text = card.oracle_text.replace("put it into its owner's", f"put {subject} into {possessor} owner's")
                self.assertNotEqual(card.oracle_text, text)
                current = compile_text(text)
                self.assertEqual("exact", current.status)
                departs, = (node for node in current.faces[0].nodes if node.event == "permanent.leave")
                self.assertEqual((baseline.effects, baseline.event_condition), (departs.effects, departs.event_condition))
        mismatched = card.oracle_text.replace("put it into its owner's", "put her into his owner's")
        self.assertNotEqual("exact", compile_text(mismatched).status)

    def test_actual_bontu_excludes_its_own_incarnation_and_draws_for_one_other_permanent(self):
        game = self.session(76271112)
        source = self.add(game.engine, "God-Eternal Bontu", zone="hand")
        land = self.add(game.engine, "Forest")
        initial = self.actual_draws(game)
        self.start(game, source, mana={"C": 3, "B": 2})
        self.cast(game, "A", source)
        self.choice(game, (land.ref,))
        current = game.state.cards[source.object_id]
        self.reject(game, "A", (current.ref,))
        self.choose(game, (land.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial + 1, self.actual_draws(game))
        self.assertEqual(("battlefield", "graveyard"), (self.zone(game, source), self.zone(game, land)))
        self.replay(game, load=True)

    def test_actual_bounced_bontu_keeps_the_original_trigger_without_a_live_exclusion(self):
        game = self.session(76271115)
        source = self.add(game.engine, "God-Eternal Bontu", zone="hand")
        own = self.add(game.engine, "Forest")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        initial = self.actual_draws(game)
        self.start(game, source, bounce, mana={"C": 3, "B": 2})
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "B", bounce, [game.state.cards[source.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.zone(game, source))
        self.choice(game, (own.ref,))
        game = self.replay(game, load=True)
        self.choose(game, (own.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial + 1, self.actual_draws(game))
        self.assertEqual(("hand", "graveyard"), (self.zone(game, source), self.zone(game, own)))
        self.replay(game, load=True)

    def test_actual_witchkite_includes_artifacts_enchantments_or_tokens_and_rejects_other_creatures(self):
        game = self.session(76271113)
        source = self.add(game.engine, "Malevolent Witchkite", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        relic = self.add(game.engine, "Darksteel Relic")
        omen = self.add(game.engine, "Omen of the Sea")
        bear = self.add(game.engine, "Grizzly Bears")
        for index in (1, 2):
            self.add(game.engine, "Swamp", ref=f"witchkite-black-source-{index}")
        initial = self.actual_draws(game)
        self.start(game, source, alarm, mana={"C": 10, "W": 1})
        self.cast(game, "A", alarm)
        self.assertIsNone(self.resolve(game))
        soldiers = tuple(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual(2, len(soldiers))
        self.cast(game, "A", source)
        self.choice(game, (relic.ref, omen.ref, *(card.ref for card in soldiers)))
        self.reject(game, "A", (bear.ref,))
        self.choose(game, (omen.ref, soldiers[0].ref))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial + 2, self.actual_draws(game))
        self.assertEqual("graveyard", self.zone(game, omen))
        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, relic), self.zone(game, bear)))
        self.assertTrue("flying" in game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.replay(game, load=True)

    def test_actual_pitiless_carnage_allows_zero_after_the_normal_cast_commits(self):
        game = self.session(76271114)
        spell = self.add(game.engine, "Pitiless Carnage", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        initial = self.actual_draws(game)
        self.start(game, spell, mana={"C": 4, "B": 1})
        self.cast(game, "A", spell)
        self.assertEqual("battlefield", self.zone(game, bear))
        self.choice(game, (bear.ref,))
        self.choose(game, ())
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial, self.actual_draws(game))
        self.assertEqual("battlefield", self.zone(game, bear))
        self.replay(game, load=True)

    def test_five_whole_sources_keep_the_exact_domain_and_independent_count_authority(self):
        cases = (
            ("permanents", register_card(self.db, self.registry, "Renounce")),
            ("artifact_creature_land", register_card(self.db, self.registry, "Reprocess")),
            ("permanents", register_card(self.db, self.registry, "Pitiless Carnage")),
            ("other_permanents", register_card(self.db, self.registry, "God-Eternal Bontu")),
            ("artifact_enchantment_token", register_card(self.db, self.registry, "Malevolent Witchkite")),
        )
        for expected, (report, semantics) in cases:
            with self.subTest(oracle_id=report["oracle_id"]):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(report["oracle_id"])
                            if ANY_NUMBER_SACRIFICE_CAPABILITY in p.capability_dependencies)
                domain, _followup = any_number_sacrifice_parts(program.effects[0])
                self.assertEqual(expected, domain)
                self.assertIsNone(program.target_schema)
                deps = tuple(cap for cap in program.capability_dependencies if cap != ANY_NUMBER_SACRIFICE_CAPABILITY)
                without = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(without, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{ANY_NUMBER_SACRIFICE_CAPABILITY}",
                              binding["blockers"])
        card = self.db.lookup("Renounce", fuzzy=False)
        for old, new in (("any number", "two or more"), ("2 life", "X life"),
                         ("this way.", "this way. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.resolution_effect_templates.any_number_sacrifice_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

    def test_scapeshift_seals_actual_land_sacrifices_before_private_tapped_search_and_shuffle(self):
        game = self.session(76271191)
        source = self.add(game.engine, "Scapeshift", zone="hand")
        first = self.add(game.engine, "Forest", ref="scapeshift-first-land")
        borrowed = self.add(game.engine, "Forest", seat="B", ref="scapeshift-borrowed-land")
        retained = self.add(game.engine, "Forest", seat="B", ref="scapeshift-copy-model")
        game.engine.change_control(borrowed.object_id, "A", reason="Seed borrowed controlled land")
        vesuva = self.add(game.engine, "Vesuva", zone="library")
        found = self.add(game.engine, "Plains", zone="library", ref="scapeshift-found-land")
        extra = self.add(game.engine, "Plains", zone="library", ref="scapeshift-extra-land")
        replacements = [self.add(game.engine, "Rest in Peace", seat=seat, ref=f"scapeshift-redirect-{seat}")
                        for seat in "CD"]
        self.start(game, source, *replacements, mana={"C": 2, "G": 2})
        shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        self.cast(game, "A", source)
        self.assertTrue(all(self.zone(game, land) == "battlefield" for land in (first, borrowed)))
        self.choice(game, [first.ref, borrowed.ref])
        self.reject(game, "A", [retained.ref])
        self.choose(game, [first.ref, borrowed.ref])
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertTrue(all(self.zone(game, land) == "battlefield" for land in (first, borrowed)))
        game = self.replay(game, load=True)
        for _ in range(4):
            if game.state.pending_decision.kind != "replacement.order":
                break
            principal = game.pending_principals()[0]
            option = game.packet(principal, full=True)["decision"]["ctx"]["options"][0]
            answer = game.act(principal, {"action_id": "choose", "replacement": option["id"]})
            self.assertTrue(answer.ok, answer.summary)
        self.assertEqual("semantic.search", game.state.pending_decision.kind)
        self.assertTrue(all(self.zone(game, land) == "exile" for land in (first, borrowed)))
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({"minimum": 0, "maximum": 2}, packet["search_spec"]["count"])
        for seat in "BCD":
            self.assertNotIn(vesuva.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.assertEqual(shuffles, game.state.players["A"].stats.get("shuffle_count", 0))
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "choose", "search_cards": [vesuva.ref, found.ref, extra.ref]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [vesuva.ref, found.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(shuffles, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertTrue(all(self.zone(game, land) == "library" for land in (vesuva, found)))
        game = self.replay(game, load=True)
        for _ in range(5):
            if game.state.pending_decision.kind != "replacement.order":
                break
            principal = game.pending_principals()[0]
            options = game.packet(principal, full=True)["decision"]["ctx"]["options"]
            option = next((row for row in options if retained.ref in row["id"]),
                          next((row for row in options if not row.get("decline")), options[0]))
            answer = game.act(principal, {"action_id": "choose", "replacement": option["id"]})
            self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))
        returned = [game.state.cards[land.object_id] for land in (vesuva, found)]
        self.assertTrue(all(card.zone == "battlefield" and card.tapped and card.controller == "A" for card in returned))
        self.assertEqual(returned[0].zone_timestamp, returned[1].zone_timestamp)
        self.assertEqual(shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        self.replay(game, load=True)

    def test_scapeshift_zero_still_searches_and_shuffles(self):
        for has_land in (False, True):
            with self.subTest(has_land=has_land):
                game = self.session(76271192 + int(has_land))
                source = self.add(game.engine, "Scapeshift", zone="hand")
                land = self.add(game.engine, "Forest", ref="scapeshift-declined-land") if has_land else None
                candidate = self.add(game.engine, "Plains", zone="library", ref="scapeshift-zero-candidate")
                self.start(game, source, mana={"C": 2, "G": 2})
                shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
                self.cast(game, "A", source)
                if land is not None:
                    self.choice(game, [land.ref])
                    self.choose(game, [])
                else:
                    self.assertEqual("semantic.search", self.resolve(game).kind)
                self.assertEqual("semantic.search", game.state.pending_decision.kind)
                packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
                self.assertEqual({"minimum": 0, "maximum": 0}, packet["search_spec"]["count"])
                self.assertFalse(game.act("pilot:A", {"action_id": "choose", "search_cards": [candidate.ref]}).ok)
                game = self.replay(game, load=True)
                answered = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
                self.assertTrue(answered.ok, answered.summary)
                self.assertIsNone(self.resolve(game))
                if land is not None:
                    self.assertEqual("battlefield", self.zone(game, land))
                self.assertEqual("library", self.zone(game, candidate))
                self.assertEqual(shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.replay(game, load=True)


    def test_scapeshift_waits_for_the_physical_owners_graveyard_order_before_search(self):
        game = self.session(76271196)
        source = self.add(game.engine, "Scapeshift", zone="hand")
        lands = [self.add(game.engine, "Forest", seat="B", ref=f"scapeshift-ordered-{i}") for i in range(2)]
        for land in lands:
            game.engine.change_control(land.object_id, "A", reason="Seed another player's controlled land")
        self.start(game, source, mana={"C": 2, "G": 2})
        self.cast(game, "A", source)
        refs = [land.ref for land in lands]
        self.choice(game, refs)
        self.choose(game, refs)
        self.assertEqual("permanent.composition_order", game.state.pending_decision.kind)
        self.assertEqual(["pilot:B"], game.pending_principals())
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        self.assertTrue(all(self.zone(game, land) == "battlefield" for land in lands))
        before = authoritative_state_hash(game.state)
        for principal, answer in (("pilot:A", list(reversed(refs))), ("pilot:B", [refs[0], refs[0]])):
            rejected = game.act(principal, {"action_id": "order", "cards": answer})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        order = game.act("pilot:B", {"action_id": "order", "cards": list(reversed(refs))})
        self.assertTrue(order.ok, order.summary)
        self.assertEqual([land.object_id for land in reversed(lands)], game.state.players["B"].zones["graveyard"][-2:])
        self.assertEqual("semantic.search", game.state.pending_decision.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        spec = game.packet("pilot:A", full=True)["decision"]["ctx"]["search_spec"]
        self.assertEqual({"minimum": 0, "maximum": 2}, spec["count"])
        searched = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(searched.ok, searched.summary)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)


class CommittedLandSearchCompilerTests(unittest.TestCase):
    def test_committed_land_search_requires_whole_printed_resolution_and_exact_hidden_recipe(self):
        from quorune.rules.committed_land_sacrifice_search import COMMITTED_LAND_SEARCH_VERSION
        from test_qualified_zone_event_queries import query_record

        text = ("Sacrifice any number of lands. Search your library for up to that many land cards, "
                "put them onto the battlefield tapped, then shuffle.")

        def compile_text(source):
            return compile_oracle_card(replace(query_record(source), type_line="Sorcery"),
                                       capability_registry=load_default_capability_registry())

        def witness():
            ir = compile_text(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertIsNone(node.target_schema)
            self.assertIsNone(node.cost)
            domain, search = any_number_sacrifice_parts(node.effects[0])
            self.assertEqual("lands", domain)
            self.assertEqual(COMMITTED_LAND_SEARCH_VERSION, search["schema_version"])

        witness()
        with patch("quorune.compiler.resolution_effect_templates.any_number_sacrifice_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for altered in (text.replace("lands.", "permanents."), text.replace("up to that many", "that many"),
                        text.replace("battlefield tapped", "battlefield"), text.replace("then shuffle", "then draw")):
            self.assertNotEqual("exact", compile_text(altered).status)
