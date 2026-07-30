import { useEffect, useMemo, useState } from "react";
import { AgentRing } from "./charts/AgentRing";
import { CalendarHeatmap } from "./charts/CalendarHeatmap";
import { DateRangeFilter } from "./charts/DateRangeFilter";
import { TokenChart } from "./charts/TokenChart";
import { UsageDonut } from "./charts/UsageDonut";
import { parseISODate, windowDateFmt } from "@/components/charts/chart-formatters";
import { t } from "./i18n";

type Data = {
  window: { start: string; end: string };
  tokens: { date: string; input: number; output: number; reasoning: number; cache: number }[];
  tokensChartType: "bar" | "area";
  agents: { agent: string; tokens: number }[];
  skills: { name: string; count: number }[];
  mcp: { name: string; count: number }[];
  heatmap: { date: string; tokens: number }[];
};

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
        <AgentRing data={data.agents} />
      </section>
      <div className="row-two">
        <section className="block">
          <h2>{t("title.skillUsage")}</h2>
          <UsageDonut data={data.skills} />
        </section>
        <section className="block">
          <h2>{t("title.mcpUsage")}</h2>
          <UsageDonut data={data.mcp} />
        </section>
      </div>
      <section className="block">
        <h2>{t("title.activity")}</h2>
        <CalendarHeatmap data={data.heatmap} />
      </section>
    </div>
  );
}
