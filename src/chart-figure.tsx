import type { RefObject } from "react";
import type { MultiChartData } from "./csv";

export type ChartType = "bar" | "line" | "radar";
const colors = [
  "#477843",
  "#426eaa",
  "#bd6633",
  "#8558a1",
  "#398986",
  "#ae426c",
];

export function ChartFigure({
  data,
  type,
  svgRef,
}: {
  data: MultiChartData;
  type: ChartType;
  svgRef: RefObject<SVGSVGElement | null>;
}) {
  const values = data.rows.flatMap((row) => row.values);
  const low = Math.min(0, ...values),
    high = Math.max(0, ...values);
  const scale = Math.max(Math.abs(low), Math.abs(high)) || 1;
  const span = high / scale - low / scale || 1;
  const y = (v: number) => 260 - ((v / scale - low / scale) / span) * 190;
  const x = (i: number) => 78 + ((i + 0.5) * 460) / data.rows.length;
  const point = (i: number, fraction: number) => {
    const a = (i * 2 * Math.PI) / data.rows.length - Math.PI / 2;
    return [
      305 + Math.cos(a) * 100 * fraction,
      174 + Math.sin(a) * 100 * fraction,
    ];
  };
  const points = (fraction: number) =>
    data.rows.map((_, i) => point(i, fraction).join(",")).join(" ");
  return (
    <svg
      ref={svgRef}
      className="chart-svg"
      viewBox="0 0 610 360"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={data.label + "数据图表"}
    >
      <rect width="610" height="360" fill="#ffffff" />
      <text x="30" y="24" fill="#55714b" fontSize="10" fontFamily="sans-serif">
        {data.label} · 本地数据图表
      </text>
      {data.series.map((name, s) => (
        <g key={name}>
          <rect x={30 + s * 95} y="37" width="9" height="9" fill={colors[s]} />
          <text
            x={43 + s * 95}
            y="45"
            fontSize="9"
            fill="#3d4b39"
            fontFamily="sans-serif"
          >
            <title>{name}</title>
            {name.length > 10 ? name.slice(0, 9) + "…" : name}
          </text>
        </g>
      ))}
      {type === "radar" ? (
        <>
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <polygon points={points(f)} fill="none" stroke="#d6dfd0" />
              <text x="310" y={174 - 100 * f} fontSize="8" fill="#62765a">
                {Number((high * f).toPrecision(4))}
              </text>
            </g>
          ))}
          {data.rows.map((row, i) => {
            const end = point(i, 1),
              label = point(i, 1.22);
            return (
              <g key={i}>
                <line
                  x1="305"
                  y1="174"
                  x2={end[0]}
                  y2={end[1]}
                  stroke="#d6dfd0"
                />
                <text
                  x={label[0]}
                  y={label[1]}
                  textAnchor="middle"
                  fontSize="9"
                  fill="#435b39"
                >
                  <title>{row.label}</title>
                  {row.label.length > 12
                    ? row.label.slice(0, 11) + "…"
                    : row.label}
                </text>
              </g>
            );
          })}
          {data.series.map((name, s) => (
            <g key={name}>
              <polygon
                data-series={name}
                points={data.rows
                  .map((row, i) =>
                    point(i, row.values[s] / (high || 1)).join(","),
                  )
                  .join(" ")}
                fill={colors[s]}
                fillOpacity=".08"
                stroke={colors[s]}
                strokeWidth="2"
              />
              {data.rows.map((row, i) => {
                const p = point(i, row.values[s] / (high || 1));
                return (
                  <circle key={i} cx={p[0]} cy={p[1]} r="3" fill={colors[s]}>
                    <title>
                      {row.label} · {name}: {row.values[s]}
                    </title>
                  </circle>
                );
              })}
            </g>
          ))}
          <text x="305" y="315" textAnchor="middle" fontSize="9" fill="#57684e">
            共同刻度 0–{high || 1}；各指标须使用可比较的单位和方向
          </text>
        </>
      ) : (
        <>
          {Array.from({ length: 5 }, (_, i) => {
            const v = low * (1 - i / 4) + high * (i / 4);
            return (
              <g key={i}>
                <line
                  x1="65"
                  x2="560"
                  y1={y(v)}
                  y2={y(v)}
                  stroke="#e8eee0"
                  strokeDasharray="3 4"
                />
                <text
                  x="52"
                  y={y(v) + 4}
                  textAnchor="end"
                  fontSize="10"
                  fill="#718466"
                  fontFamily="sans-serif"
                >
                  {Number(v.toPrecision(4))}
                </text>
              </g>
            );
          })}
          <line x1="65" x2="560" y1={y(0)} y2={y(0)} stroke="#b8cba4" />
          {data.series.map((name, s) => (
            <g key={name} data-series={name}>
              {type === "line" && (
                <polyline
                  fill="none"
                  stroke={colors[s]}
                  strokeWidth="2.5"
                  points={data.rows
                    .map((r, i) => `${x(i)},${y(r.values[s])}`)
                    .join(" ")}
                />
              )}
              {data.rows.map((r, i) => {
                const v = r.values[s];
                const w =
                  Math.min(48, 340 / data.rows.length) / data.series.length;
                const px = x(i) + (s - (data.series.length - 1) / 2) * w;
                return (
                  <g key={i}>
                    {type === "bar" ? (
                      <rect
                        x={px - w * 0.45}
                        y={Math.min(y(0), y(v))}
                        width={w * 0.9}
                        height={Math.max(1, Math.abs(y(0) - y(v)))}
                        rx="2"
                        fill={colors[s]}
                      >
                        <title>
                          {r.label} · {name}: {v}
                        </title>
                      </rect>
                    ) : (
                      <circle
                        cx={x(i)}
                        cy={y(v)}
                        r="4"
                        fill={colors[s]}
                        stroke="white"
                        strokeWidth="1"
                      >
                        <title>
                          {r.label} · {name}: {v}
                        </title>
                      </circle>
                    )}
                    {(data.series.length === 1 ||
                      data.rows.length * data.series.length <= 24) && (
                      <text
                        x={type === "bar" ? px : x(i)}
                        y={v >= 0 ? y(v) - 10 : y(v) + 15}
                        textAnchor="middle"
                        fontSize="10"
                        fill={colors[s]}
                        fontFamily="sans-serif"
                      >
                        {Number(v.toPrecision(5))}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          ))}
          {data.rows.map((r, i) => (
            <text
              key={i}
              x={x(i)}
              y="295"
              textAnchor="middle"
              fontSize={data.rows.length > 8 ? "7" : "10"}
              fill="#627859"
              fontFamily="sans-serif"
            >
              <title>{r.label}</title>
              {r.label.length > 10 ? r.label.slice(0, 9) + "…" : r.label}
            </text>
          ))}
        </>
      )}
      <text
        x="305"
        y="345"
        textAnchor="middle"
        fontSize="8"
        fill="#78876e"
        fontFamily="sans-serif"
      >
        研析 · 由用户输入数据生成 · 请核对来源
      </text>
    </svg>
  );
}
