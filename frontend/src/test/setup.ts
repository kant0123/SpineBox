// Node 26 はグローバルの `localStorage` を既定で持つが、`--localstorage-file` を
// 渡さないと `undefined` を返す。jsdom は `window._localStorage` に実装を用意するが、
// Node 側のグローバル(値 undefined)が `window.localStorage` のアクセサを覆い隠すため、
// 裸の `localStorage` も `window.localStorage` も undefined になる (#130)。
// jsdom が用意した実装を両方に割り当てる。
const jsdomLocalStorage = (window as { _localStorage?: Storage })._localStorage;
if (jsdomLocalStorage) {
  for (const target of [globalThis, window]) {
    try {
      Object.defineProperty(target, "localStorage", {
        value: jsdomLocalStorage,
        writable: true,
        configurable: true,
      });
    } catch {
      (target as { localStorage?: Storage }).localStorage = jsdomLocalStorage;
    }
  }
}

const jsdomSessionStorage = (window as { _sessionStorage?: Storage })._sessionStorage;
if (jsdomSessionStorage && (typeof globalThis.sessionStorage === "undefined" || globalThis.sessionStorage == null)) {
  for (const target of [globalThis, window]) {
    try {
      Object.defineProperty(target, "sessionStorage", {
        value: jsdomSessionStorage,
        writable: true,
        configurable: true,
      });
    } catch {
      (target as { sessionStorage?: Storage }).sessionStorage = jsdomSessionStorage;
    }
  }
}

// jsdom は <dialog> の showModal() / close() を実装していない。open 属性と close イベントだけ最小限に再現する。
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.show = function show(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement, returnValue?: string) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
    if (returnValue !== undefined) this.returnValue = returnValue;
    this.dispatchEvent(new Event("close"));
  };
}
