import { AreaChart, Area } from "@/components/charts/area-chart";
import { Grid } from "@/components/charts/grid";
import { XAxis } from "@/components/charts/x-axis";
import { YAxis } from "@/components/charts/y-axis";
import { ChartTooltip } from "@/components/charts/tooltip";
import { SERIES_COLORS } from "./names";
import { t } from "@/i18n";

export type TokenPoint = {
  date: string;
  input: number;
  output: number;
  reasoning: number;
  cache: number;
};

export function TokenArea({ data, inView }: { data: TokenPoint[]; inView: boolean }) {
  if (data.length === 0) return <div className="empty">{t("state.noTokenActivity")}</div>;

  return (
    <>
      <AreaChart
        data={data}
        xDataKey="date"
        aspectRatio="3 / 1"
        margin={{ left: 54 }}
        status={inView ? "ready" : "loading"}
      >
        <Grid horizontal />
        <Area dataKey="input" fill={SERIES_COLORS.input} />
        <Area dataKey="output" fill={SERIES_COLORS.output} />
        <Area dataKey="reasoning" fill={SERIES_COLORS.reasoning} />
        <Area dataKey="cache" fill={SERIES_COLORS.cache} />
        <YAxis formatLargeNumbers />
        <XAxis />
        <ChartTooltip
          showDatePill
          rows={(p) => [
            { label: t("legend.input"), value: p.input as number, color: SERIES_COLORS.input },
            { label: t("legend.output"), value: p.output as number, color: SERIES_COLORS.output },
            {
              label: t("legend.reasoning"),
              value: p.reasoning as number,
              color: SERIES_COLORS.reasoning,
            },
            { label: t("legend.cache"), value: p.cache as number, color: SERIES_COLORS.cache },
          ]}
        />
      </AreaChart>
      <div className="legend">
        {(["input", "output", "reasoning", "cache"] as const).map((k) => (
          <span className="item" key={k}>
            <span className="swatch" style={{ background: SERIES_COLORS[k] }} />
            {t(`legend.${k}`)}
          </span>
        ))}
      </div>
    </>
  );
}
