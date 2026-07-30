export const AGENT_NAMES: Record<string, string> = {
  hermes_agent: "Hermes",
  claude_code: "Claude Code",
  codex: "Codex",
};

export function agentLabel(agent: string): string {
  return AGENT_NAMES[agent] ?? agent;
}

export const AGENT_COLORS: Record<string, string> = {
  hermes_agent: "#FFFFFF",
  claude_code: "#EB643D",
  codex: "#6074F1",
};

export function agentColor(agent: string, fallback: string): string {
  return AGENT_COLORS[agent] ?? fallback;
}

// Flat categorical palette (no gradients).
export const SERIES_COLORS = {
  input: "#60A5FA",
  output: "#34D399",
  reasoning: "#A78BFA",
  cache: "#F472B6",
};

export const CATEGORY_COLORS = [
  "#60A5FA",
  "#34D399",
  "#A78BFA",
  "#F472B6",
  "#FBBF24",
  "#22D3EE",
  "#F87171",
  "#9CA3AF",
];
