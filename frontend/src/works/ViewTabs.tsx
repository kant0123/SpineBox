import { volumesHref, worksHref } from "../route";

/** 一覧の表示の切り替え(作品 / 巻)。ブックマークの入口はサイドバー(#85)にある。 */
export function ViewTabs({ current }: { current: "works" | "volumes" }) {
  return (
    <nav className="view-tabs" aria-label="表示">
      <a href={worksHref} aria-current={current === "works" ? "page" : undefined}>
        作品
      </a>
      <a href={volumesHref} aria-current={current === "volumes" ? "page" : undefined}>
        巻
      </a>
    </nav>
  );
}
