import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { canonicalUrl } from "./route";
import { applyTheme, loadTheme } from "./shell/theme";
import "./styles.css";

const canonical = canonicalUrl(window.location.pathname, window.location.hash);
if (canonical) window.history.replaceState(null, "", canonical);
// 描画の前に選んだテーマを付ける(ダークで開いたときに一瞬ライトになるのを避ける。全画面のリーダーから開いたときも)
applyTheme(loadTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
