import { useEffect, useState } from "react";
import { parseRoute, type Route } from "./route";
import { Library } from "./library/Library";
import { Reader } from "./reader/Reader";
import { Duplicates } from "./duplicates/Duplicates";
import { WorkPage } from "./works/WorkPage";
import { WorkProposals } from "./works/WorkProposals";
import { Works } from "./works/Works";
import { OrganizeHistory, OrganizePlan } from "./organize/Organize";

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  switch (route.name) {
    case "read":
      return <Reader key={route.id} id={route.id} />;
    case "organize-plan":
      return <OrganizePlan key={route.id} volumeId={route.id} />;
    case "organize-history":
      return <OrganizeHistory />;
    case "duplicates":
      return <Duplicates />;
    case "works":
      return <Works />;
    case "work":
      return <WorkPage key={route.id} id={route.id} />;
    case "work-proposals":
      return <WorkProposals />;
    default:
      return <Library />;
  }
}
