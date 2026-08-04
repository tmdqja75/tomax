import { useEffect, useMemo, useState } from "react";
import { AgentRing } from "./charts/AgentRing";
import { CalendarHeatmap } from "./charts/CalendarHeatmap";
import { DateRangeFilter } from "./charts/DateRangeFilter";
import { ModelBar } from "./charts/ModelBar";
import { TokenChart } from "./charts/TokenChart";
import { UsageDonut } from "./charts/UsageDonut";
import { parseISODate, windowDateFmt } from "@/components/charts/chart-formatters";
import { t } from "./i18n";

type HeatDatum = {
  date: string;
  tokens: number;
  byAgent?: { agent: string; tokens: number }[];
  bySkill?: { name: string; count: number }[];
  byMcp?: { name: string; count: number }[];
  byModel?: { name: string; count: number }[];
};

type Data = {
  window: { start: string; end: string };
  tokens: { date: string; input: number; output: number; reasoning: number; cache: number }[];
  tokensChartType: "bar" | "area";
  agents: { agent: string; tokens: number }[];
  skills: { name: string; count: number }[];
  mcp: { name: string; count: number }[];
  models: { name: string; count: number }[];
  heatmap: HeatDatum[];
  pieTopN: number;
};

const OTHER_LABEL = "Other";

// Mirrors bucket_top_n in src/tomax/render/_counters.py so re-aggregating
// per-day skill/MCP counts over an arbitrary date range stays consistent
// with the server's full-window bucketing.
function bucketTopN(counts: Map<string, number>, topN: number): { name: string; count: number }[] {
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const kept = ranked.slice(0, topN).map(([name, count]) => ({ name, count }));
  const overflow = ranked.slice(topN);
  if (overflow.length > 0) {
    kept.push({ name: OTHER_LABEL, count: overflow.reduce((sum, [, c]) => sum + c, 0) });
  }
  return kept;
}

function sumAgentTokens(entries: HeatDatum[]): { agent: string; tokens: number }[] {
  const totals = new Map<string, number>();
  for (const entry of entries) {
    for (const { agent, tokens } of entry.byAgent ?? []) {
      totals.set(agent, (totals.get(agent) ?? 0) + tokens);
    }
  }
  return [...totals.entries()].map(([agent, tokens]) => ({ agent, tokens }));
}

function sumCounts(entries: HeatDatum[], pick: (d: HeatDatum) => { name: string; count: number }[] | undefined) {
  const totals = new Map<string, number>();
  for (const entry of entries) {
    for (const { name, count } of pick(entry) ?? []) {
      totals.set(name, (totals.get(name) ?? 0) + count);
    }
  }
  return totals;
}

export default function App() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<{ from: string; to: string } | null>(null);

  useEffect(() => {
    fetch("data.json")
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  const tokenDates = useMemo(() => data?.tokens.map((p) => p.date) ?? [], [data]);
  const tokenMin = tokenDates[0];
  const tokenMax = tokenDates[tokenDates.length - 1];

  useEffect(() => {
    if (tokenMin && tokenMax) setRange({ from: tokenMin, to: tokenMax });
  }, [tokenMin, tokenMax]);

  const filteredTokens = useMemo(() => {
    if (!data || !range) return data?.tokens ?? [];
    return data.tokens.filter((p) => p.date >= range.from && p.date <= range.to);
  }, [data, range]);

  const filteredHeatmap = useMemo(() => {
    if (!data || !range) return data?.heatmap ?? [];
    return data.heatmap.filter((p) => p.date >= range.from && p.date <= range.to);
  }, [data, range]);

  const filteredAgents = useMemo(() => sumAgentTokens(filteredHeatmap), [filteredHeatmap]);

  const filteredSkills = useMemo(
    () => bucketTopN(sumCounts(filteredHeatmap, (d) => d.bySkill), data?.pieTopN ?? 6),
    [filteredHeatmap, data?.pieTopN],
  );

  const filteredMcp = useMemo(
    () => bucketTopN(sumCounts(filteredHeatmap, (d) => d.byMcp), data?.pieTopN ?? 6),
    [filteredHeatmap, data?.pieTopN],
  );

  const filteredModels = useMemo(
    () => [...sumCounts(filteredHeatmap, (d) => d.byModel)].map(([name, count]) => ({ name, count })),
    [filteredHeatmap],
  );

  if (error) return <div className="dashboard empty">{t("state.error")} {error}</div>;
  if (!data) return <div className="dashboard empty">{t("state.loading")}</div>;

  return (
    <div className="dashboard">
      <section className="block">
        <h2>
          {t("title.tokenUsage")}{" "}
          <span className="window">
            {windowDateFmt.format(parseISODate(data.window.start))} →{" "}
            {windowDateFmt.format(parseISODate(data.window.end))}
          </span>
        </h2>
        {tokenMin && tokenMax && range && (
          <DateRangeFilter
            min={tokenMin}
            max={tokenMax}
            from={range.from}
            to={range.to}
            onChange={setRange}
          />
        )}
        <TokenChart data={filteredTokens} useBarChart={data.tokensChartType === "bar"} />
      </section>
      <section className="block">
        <h2>{t("title.usageByAgent")}</h2>
        <AgentRing data={filteredAgents} />
      </section>
      <section className="block">
        <h2>{t("title.modelUsage")}</h2>
        <ModelBar data={filteredModels} />
      </section>
      <div className="row-two">
        <section className="block">
          <h2>{t("title.skillUsage")}</h2>
          <UsageDonut data={filteredSkills} />
        </section>
        <section className="block">
          <h2>{t("title.mcpUsage")}</h2>
          <UsageDonut data={filteredMcp} />
        </section>
      </div>
      <section className="block">
        <h2>{t("title.activity")}</h2>
        <CalendarHeatmap data={filteredHeatmap} />
      </section>
    </div>
  );
}
