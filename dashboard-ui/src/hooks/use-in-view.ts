import { type RefObject, useEffect, useRef, useState } from "react";

/** True once the ref'd element has scrolled into the viewport (fires once, then disconnects). */
export function useInView<T extends HTMLElement>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Playwright's dashboard export sets prefers-reduced-motion: reduce and never scrolls —
    // reveal everything immediately so the static screenshot isn't missing below-the-fold charts.
    const skipsMotion =
      typeof IntersectionObserver === "undefined" ||
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (skipsMotion) {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { threshold: 0.2, rootMargin: "0px 0px -60px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, inView];
}
