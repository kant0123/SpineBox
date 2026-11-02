/** バックエンド(spinebox/naming.py normalize_title)と同じ正規化: NFKC・小文字・カタカナ → ひらがな・記号と空白を落とす */
export function normalizeSearch(text: string): string {
  return Array.from(text.normalize("NFKC").toLowerCase())
    .map((ch) => (ch >= "ァ" && ch <= "ヶ" ? String.fromCharCode(ch.charCodeAt(0) - 0x60) : ch))
    .filter((ch) => /[\p{L}\p{N}]/u.test(ch))
    .join("");
}
