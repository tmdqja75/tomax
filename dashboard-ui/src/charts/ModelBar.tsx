import { BarChart } from "@/components/charts/bar-chart";
import { Bar } from "@/components/charts/bar";
import { BarYAxis } from "@/components/charts/bar-y-axis";
import { Grid } from "@/components/charts/grid";
import { ChartTooltip } from "@/components/charts/tooltip";
import { CATEGORY_COLORS } from "./names";
import { t } from "@/i18n";

export type ModelDatum = { name: string; count: number };

export function ModelBar({ data }: { data: ModelDatum[] }) {
  const active = data.filter((d) => d.count > 0);
  if (active.length === 0) return <div className="empty">{t("state.noData")}</div>;

  const ranked = [...active].sort((a, b) => b.count - a.count);

  return (
    <BarChart
      data={ranked}
      xDataKey="name"
      orientation="horizontal"
      aspectRatio="3 / 1"
      margin={{ left: 110 }}
      barGap={0.25}
    >
      <Grid vertical />
      <Bar dataKey="count" fill={CATEGORY_COLORS[0]} />
      <BarYAxis />
      <ChartTooltip
        rows={(p) => [{ label: p.name as string, value: p.count as number, color: CATEGORY_COLORS[0] }]}
      />
    </BarChart>
  );
}
