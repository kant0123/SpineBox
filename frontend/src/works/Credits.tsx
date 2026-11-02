import type { SourceName } from "../api";
import { SOURCE_SITES } from "./labels";

/** 公開データベースの利用表示。国立国会図書館サーチは API を利用していることの明示が条件(#45)。
 *  取得元が有効なときだけ、その取得元を挙げる。 */
export function Credits({ sources }: { sources: SourceName[] }) {
  const shown = (Object.keys(SOURCE_SITES) as SourceName[]).filter((s) => sources.includes(s));
  if (shown.length === 0) return null;
  return (
    <footer className="credits" aria-label="利用している公開データベース">
      <p>
        作品の情報は、
        {shown.map((s, i) => (
          <span key={s}>
            {i > 0 && "、"}
            <a href={SOURCE_SITES[s].url} target="_blank" rel="noreferrer noopener">
              {SOURCE_SITES[s].name}
            </a>
            ({SOURCE_SITES[s].api})
          </span>
        ))}
        から取得しています。
      </p>
    </footer>
  );
}
