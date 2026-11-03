import { useState } from "react";
import { api, type VolumeMetaField, type WorkVolume } from "../api";
import { fieldNote } from "./labels";

const FIELDS: { name: "publisher" | "label" | "released" | "isbn"; label: string; hint?: string }[] = [
  { name: "publisher", label: "出版社" },
  { name: "label", label: "レーベル" },
  { name: "released", label: "発売日", hint: "2013-05 のように" },
  { name: "isbn", label: "ISBN" },
];

/** 巻の書誌(国立国会図書館サーチ由来・手入力)。項目ごとに出所を示し、手で直したものは自動に戻せる。 */
export function VolumeMeta({
  v,
  busy,
  run,
  onChanged,
}: {
  v: WorkVolume;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const meta = v.metadata ?? {};
  const get = (n: VolumeMetaField) => meta[n]?.value ?? "";
  const [draft, setDraft] = useState<Record<string, string>>({});
  const value = (n: string) => draft[n] ?? get(n as VolumeMetaField);
  const changed = FIELDS.filter((f) => value(f.name) !== get(f.name));
  const summary = FIELDS.map((f) => get(f.name)).filter(Boolean).join(" / ");

  const save = () => {
    const body: Record<string, string> = {};
    for (const f of changed) body[f.name] = value(f.name);
    void run(async () => {
      await api.editVolumeMetadata(v.id, body);
      setDraft({});
      await onChanged();
    });
  };

  return (
    <details className="volume-bib">
      <summary>書誌{summary ? `: ${summary}` : "(未取得)"}</summary>
      <form
        className="work-edit"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        {FIELDS.map((f) => {
          const info = meta[f.name];
          return (
            <div key={f.name} className="bib-field">
              <label>
                この巻の{f.label}
                {f.hint && <span id={`bib-hint-${v.id}-${f.name}`} className="organize-hint muted">例: {f.hint}</span>}
                <input value={value(f.name)} onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })} aria-describedby={f.hint ? `bib-hint-${v.id}-${f.name}` : undefined} />
              </label>
              <span className="volume-meta">
                {fieldNote(info?.from ? { ...info, sources: [info.from] } : info)}
                {info?.url && (
                  <>
                    {" "}
                    <a href={info.url} target="_blank" rel="noreferrer noopener">
                      出典
                    </a>
                  </>
                )}
                {info?.manual && (
                  <button
                    type="button"
                    className="small secondary"
                    disabled={busy}
                    aria-label={`${f.label}を自動に戻す`}
                    onClick={() =>
                      void run(async () => {
                        await api.resetVolumeMetadata(v.id, f.name);
                        setDraft({});
                        await onChanged();
                      })
                    }
                  >
                    自動に戻す
                  </button>
                )}
              </span>
            </div>
          );
        })}
        <button type="submit" className="small" disabled={busy || changed.length === 0}>
          書誌を保存
        </button>
      </form>
    </details>
  );
}
