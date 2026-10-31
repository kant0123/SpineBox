import { useEffect, useState } from "react";
import { parseRoute, type Route } from "./route";
import { Library } from "./library/Library";
import { Reader } from "./reader/Reader";

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return route.name === "read" ? <Reader key={route.id} id={route.id} /> : <Library />;
}
