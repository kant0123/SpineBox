// サイドバーにあって、まだ中身の無い画面(#85 の枠)。フォローは #87、ブックマークは #86 で中身を差し込む。
export function Pending({ title, text }: { title: string; text: string }) {
  return (
    <div className="page">
      <header className="page-header">
        <h1>{title}</h1>
      </header>
      <p className="muted">{text}</p>
    </div>
  );
}
