import type { DeckSubmission } from "./api";

export function deckSubmissionNotice({ preflight, format_legality }: DeckSubmission) {
  if (!preflight.trusted_only_ready) {
    return {
      kind: "warning",
      message: preflight.card_rules_ready
        ? "All cards passed rules verification. The Commander format audit is incomplete."
        : "Some cards lack fully verified rules support. Unsupported actions can pause play.",
    } as const;
  }
  if (format_legality.status === "preview_override_confirmed") {
    return {
      kind: "warning",
      message: "Preview-card legality warning confirmed. The exact override is recorded with this list.",
    } as const;
  }
  return { kind: "success", message: "Deck and Commander rules checks passed." } as const;
}
