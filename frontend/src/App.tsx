import { useEffect, useState } from "react";
import { parseRoute, type Route } from "./route";
import { Admin } from "./admin/Admin";
import { FacetList } from "./browse/FacetList";
import { Reader } from "./reader/Reader";
import { AppShell } from "./shell/AppShell";
import { LibraryScopeProvider } from "./shell/libraryScope";
import { WorkPage } from "./works/WorkPage";
import { WorkTitles } from "./works/WorkTitles";
import { Review } from "./review/Review";
import { ReviewCountsProvider } from "./review/reviewCounts";
import { Works } from "./works/Works";
import { Inbox } from "./organize/Inbox";
import { FollowReleases } from "./follows/Follows";
import { Bookmarks } from "./bookmarks/Bookmarks";
import { OrganizeHistory, OrganizePlan, WorkOrganize } from "./organize/Organize";

/** サイドバーの中に出す画面 */
function Page({ route }: { route: Route }) {
  switch (route.name) {
    case "works":
      return <Works key={JSON.stringify(route.scope)} scope={route.scope} />;
    case "organize-plan":
      return <OrganizePlan key={`${route.id}/${route.naming}`} volumeId={route.id} naming={route.naming} />;
    case "work-organize":
      return <WorkOrganize key={route.id} workId={route.id} />;
    case "organize-history":
      return <OrganizeHistory />;
    case "inbox":
      return <Inbox />;
    case "organize-files":
      return <Admin tab="data" focus="organize" />;
    case "work":
      return <WorkPage key={route.id} id={route.id} />;
    case "work-titles":
      return <WorkTitles key={route.id} id={route.id} />;
    case "review":
      return <Review tab={route.tab} />;
    case "facets":
      return <FacetList key={route.field} field={route.field} />;
    case "follows":
      return <FollowReleases />;
    case "bookmarks":
      return <Bookmarks />;
    case "admin":
      return <Admin tab={route.tab} />;
    case "read":
      return null; // 全画面(下の App で出す)
  }
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <LibraryScopeProvider>
      {route.name === "read" ? ( // リーダーはサイドバーを出さず全画面
        <Reader key={route.id} id={route.id} />
      ) : (
        <ReviewCountsProvider trigger={route}>
          <AppShell route={route}>
            <Page route={route} />
          </AppShell>
        </ReviewCountsProvider>
      )}
    </LibraryScopeProvider>
  );
}
