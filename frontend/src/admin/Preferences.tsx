// 管理の好みタブ(#85)。項目ごとに置き場所を決めている:
// - 表示のテーマ・リーダーの既定(表紙単独・小説の文字サイズ): 端末ごと(localStorage)。
//   スマホと PC で好みが違うことが多く、リーダーの中で変えたものと同じ場所に置く(リーダーの中で変えても、ここで変えても同じ値)
// - 未翻訳のジャンル・タグを出すか・キャッシュの上限・取得元: サーバーの設定ファイル(読み取りだけ)。
//   DB に入る値や容量が変わる設定なので、画面からは変えない
import { useEffect, useState } from "react";
import { api, type ServerSettings } from "../api";
import { DEFAULT_FONT_SIZE, FONT_SIZES } from "../novel/pagination";
import { loadNovelSettings, loadSettings, saveNovelSettings, saveSettings, type ReaderSettings } from "../settings";
import { useTheme, type ThemeChoice } from "../shell/theme";
import { formatBytes } from "./format";

const THEME_LABELS: Record<ThemeChoice, string> = { system: "OS の設定に従う", light: "ライト", dark: "ダーク" };
const SOURCE_LABELS: Record<string, string> = {
  ndl: "国立国会図書館サーチ", mediaarts: "メディア芸術データベース", bangumi: "Bangumi", nhentai: "nHentai", ehentai: "E-Hentai",
};

export function Preferences() {
  const { choice, choose } = useTheme();
  const [reader, setReader] = useState<ReaderSettings>(loadSettings);
  const [fontSize, setFontSize] = useState(() => loadNovelSettings(FONT_SIZES, DEFAULT_FONT_SIZE).fontSize);
  const [server, setServer] = useState<ServerSettings | null>(null);
  const [serverError, setServerError] = useState(false);

  useEffect(() => {
    api.serverSettings().then(setServer, () => setServerError(true));
  }, []);

  const updateReader = (patch: Partial<ReaderSettings>) => {
    const next = { ...reader, ...patch };
    saveSettings(next);
    setReader(next);
  };
  const updateFont = (size: number) => {
    saveNovelSettings({ fontSize: size });
    setFontSize(size);
  };

  return (
    <div className="prefs">
      <h2>表示</h2>
      <p className="muted prefs-note">この端末のブラウザに記憶します。</p>
      <fieldset className="prefs-group">
        <legend>テーマ</legend>
        {(Object.keys(THEME_LABELS) as ThemeChoice[]).map((t) => (
          <label key={t} className="prefs-choice">
            <input type="radio" name="theme" value={t} checked={choice === t} onChange={() => choose(t)} />
            {THEME_LABELS[t]}
          </label>
        ))}
      </fieldset>

      <h2>リーダーの既定</h2>
      <p className="muted prefs-note">この端末のブラウザに記憶します。</p>
      <label className="prefs-choice prefs-check">
        <input type="checkbox" checked={reader.coverAlone} onChange={(e) => updateReader({ coverAlone: e.target.checked })} />
        表紙を単独で表示する(次に巻を開くときから)
      </label>
      <label className="prefs-field">
        小説の文字サイズ
        <select value={fontSize} onChange={(e) => updateFont(Number(e.target.value))}>
          {FONT_SIZES.map((s) => (
            <option key={s} value={s}>{s}px{s === DEFAULT_FONT_SIZE ? "(既定)" : ""}</option>
          ))}
        </select>
      </label>

      <h2>サーバーの設定</h2>
      <p className="muted prefs-note">サーバー機の設定ファイル(config.toml)の値です。変えるには設定ファイルを直してサーバーを再起動してください。</p>
      {serverError && <p className="notice warn">サーバーの設定を読み込めませんでした。</p>}
      {server && (
        <dl className="prefs-server">
          <dt>未翻訳のジャンル・タグ</dt>
          <dd>{server.untranslated_terms === "keep" ? "英語のまま出す(keep)" : "出さない(hide)"}<code>[metadata] untranslated_terms</code></dd>
          <dt>公開データベースの取得元</dt>
          <dd>{server.metadata_sources.length ? server.metadata_sources.map((s) => SOURCE_LABELS[s] ?? s).join(" / ") : "使わない"}
            {server.metadata_background ? "(裏で少しずつ取得)" : ""}</dd>
          <dt>展開キャッシュの上限</dt>
          <dd>{formatBytes(server.cache_limit_bytes)}</dd>
          <dt>ページのキャッシュ(RAM)の上限</dt>
          <dd>{server.memory_cache_limit_bytes > 0 ? formatBytes(server.memory_cache_limit_bytes) : "使わない"}</dd>
        </dl>
      )}
    </div>
  );
}
