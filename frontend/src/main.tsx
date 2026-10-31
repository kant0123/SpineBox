import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { canonicalUrl } from "./route";
import "./styles.css";

const canonical = canonicalUrl(window.location.pathname, window.location.hash);
if (canonical) window.history.replaceState(null, "", canonical);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
