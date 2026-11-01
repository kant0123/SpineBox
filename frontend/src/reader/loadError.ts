import { ApiError } from "../api";

/** 巻情報(`GET /api/volumes/{id}`)の取得に失敗したときに、リーダーに出す文言。 */
export function volumeErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) {
    // fetch 自体の失敗(サーバー停止・LAN から外れた)や、応答が JSON でないとき
    return "この巻を開けませんでした。サーバーに接続できているか確かめてください。";
  }
  switch (err.status) {
    case 404:
      return "この巻を開けませんでした。蔵書から無くなっています。一覧でスキャンし直してください。";
    case 409:
      return "この巻を開けませんでした。蔵書フォルダがオフラインです(外付けドライブが外れていないか確かめてください)。";
    case 422:
      // 壊れたアーカイブ・展開上限の超過など。理由はバックエンドの文言をそのまま出す
      return err.detail ?? "この巻を開けませんでした。アーカイブを読めません。";
    default:
      return `この巻を開けませんでした(サーバーエラー ${err.status})。`;
  }
}
