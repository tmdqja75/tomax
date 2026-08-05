import { TokenArea, type TokenPoint } from "./TokenArea";
import { TokenBar } from "./TokenBar";

export function TokenChart({
  data,
  useBarChart,
  inView,
}: {
  data: TokenPoint[];
  useBarChart: boolean;
  inView: boolean;
}) {
  return useBarChart ? (
    <TokenBar data={data} inView={inView} />
  ) : (
    <TokenArea data={data} inView={inView} />
  );
}
