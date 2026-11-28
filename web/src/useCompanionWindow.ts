import { useEffect, useRef, useState } from "react";

export function useCompanionWindow(title: string, features: string) {
  const companion = useRef<Window | null>(null);
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    const close = () => companion.current?.close();
    window.addEventListener("pagehide", close);
    return () => {
      window.removeEventListener("pagehide", close);
      close();
    };
  }, []);

  function open() {
    if (companion.current && !companion.current.closed) {
      companion.current.focus();
      return true;
    }
    const popup = window.open("", "_blank", `popup,${features}`);
    if (!popup) return false;
    companion.current = popup;
    popup.document.title = title;
    popup.document.documentElement.lang = document.documentElement.lang || "en";
    for (const source of document.querySelectorAll<HTMLLinkElement | HTMLStyleElement>("link[rel=stylesheet], style")) {
      const style = source.cloneNode(true) as HTMLLinkElement | HTMLStyleElement;
      if (style instanceof HTMLLinkElement && source instanceof HTMLLinkElement) style.href = source.href;
      popup.document.head.append(style);
    }
    const node = popup.document.createElement("div");
    popup.document.body.append(node);
    popup.addEventListener("pagehide", () => {
      if (companion.current !== popup) return;
      companion.current = null;
      setHost(null);
    }, { once: true });
    setHost(node);
    return true;
  }

  return { host, open };
}
