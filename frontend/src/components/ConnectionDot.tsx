import type { SocketStatus } from "../net/socket";

const LABEL: Record<SocketStatus, string> = {
  connecting: "connecting",
  open: "live",
  reconnecting: "reconnecting",
  closed: "offline",
};

/**
 * Connection state, always visible and never modal (spec §09). A dropped
 * player should be able to tell at a glance whether the game broke or they
 * just walked into a tunnel — without a dialog stealing the round from them.
 */
export function ConnectionDot({ status }: { status: SocketStatus }) {
  return (
    <span className={`conn ${status}`} role="status">
      <i aria-hidden="true" />
      {LABEL[status]}
    </span>
  );
}
