const colorNames: Record<string, string> = {
  W: "white mana", U: "blue mana", B: "black mana", R: "red mana", G: "green mana", C: "colorless mana",
};
const otherSymbols: Record<string, [string, string]> = {
  T: ["tap", "tap"], Q: ["untap", "untap"], S: ["s", "snow mana"], E: ["energy", "energy"],
  X: ["x", "X mana"], Y: ["y", "Y mana"], Z: ["z", "Z mana"], "∞": ["infinity", "infinite mana"],
};
const hybrids = new Set(["WU", "WB", "UB", "UR", "BR", "BG", "RW", "RG", "GW", "GU"]);

function glyphFor(code: string): [string, string] | undefined {
  const name = colorNames[code];
  if (name) return [code.toLowerCase(), name];
  if (otherSymbols[code]) return otherSymbols[code];
  if (/^(?:0|[1-9]|1\d|20|100|1000000)$/.test(code)) return [code, `${code} generic mana`];
  const [first, second, third, fourth] = code.split("/");
  if (!first || !second || fourth || third && third !== "P") return;
  if (/^[WUBRG]$/.test(first) && second === "P" && !third) {
    return [`${first.toLowerCase()}p`, `${colorNames[first]} or 2 life`];
  }
  if ((first === "2" || first === "C") && /^[WUBRG]$/.test(second) && !third) {
    return [`${first.toLowerCase()}${second.toLowerCase()}`, `${colorNames[first] ?? "2 generic mana"} or ${colorNames[second]}`];
  }
  const pair = hybrids.has(first + second) ? first + second : second + first;
  if (hybrids.has(pair)) {
    return [`${pair.toLowerCase()}${third ? "p" : ""}`, `${colorNames[first]} or ${colorNames[second]}${third ? " or 2 life" : ""}`];
  }
}

export function ManaText({ text, className = "" }: { text: string; className?: string }) {
  return <span className={`mana-text${className ? ` ${className}` : ""}`}>
    {text.split(/(\{[^{}]+\})/g).map((part, index) => {
      const glyph = part.startsWith("{") && part.endsWith("}") ? glyphFor(part.slice(1, -1).toUpperCase()) : undefined;
      if (!glyph) return part;
      return <i className={`ms ms-cost ms-${glyph[0]}`} role="img" aria-label={glyph[1]} key={index}>
        <span className="mana-symbol-source" aria-hidden="true">{part}</span>
      </i>;
    })}
  </span>;
}

export function spokenMana(text: string): string {
  return text.replace(/\{([^{}]+)\}/g, (symbol, code: string) => ` ${glyphFor(code.toUpperCase())?.[1] ?? symbol} `)
    .replace(/\s+/g, " ").trim();
}
