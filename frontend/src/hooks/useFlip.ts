import { useEffect, useLayoutEffect, useRef } from "react";

import { flipDeltas } from "../ui/flip";

const DURATION_MS = 420;
const EASING = "cubic-bezier(0.2, 0.9, 0.2, 1)";

/** Layout effects cannot run without a DOM. Picking the hook once, at module
 * load, keeps call order stable while letting the component render on the
 * server (where there is nothing to measure and nothing to animate). */
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * FLIP for a keyed list (spec §09: rank changes are the reward moment).
 *
 * Returns a ref callback to attach to each row. After every commit it
 * measures where rows landed, compares against the previous frame, and plays
 * each moved row from its old position to its new one — so an overtake reads
 * as movement rather than as a value blinking.
 *
 * The measurement runs in useLayoutEffect, before paint, so the inverted
 * transform is applied in the same frame the browser would have shown the
 * jump. Web Animations API only: no library, and the animation runs off the
 * main thread.
 */
export function useFlip(): (key: string) => (el: HTMLElement | null) => void {
  const nodes = useRef(new Map<string, HTMLElement>());
  const previous = useRef(new Map<string, number>());

  useIsomorphicLayoutEffect(() => {
    const current = new Map<string, number>();
    nodes.current.forEach((el, key) => current.set(key, el.getBoundingClientRect().top));

    if (!prefersReducedMotion()) {
      flipDeltas(previous.current, current).forEach((delta, key) => {
        nodes.current.get(key)?.animate(
          [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
          { duration: DURATION_MS, easing: EASING },
        );
      });
    }
    previous.current = current;
  });

  return (key: string) => (el: HTMLElement | null) => {
    if (el === null) nodes.current.delete(key);
    else nodes.current.set(key, el);
  };
}
