import { useEffect, useState } from "react";

import { fetchRankings, type Ranked } from "../net/menu";
import { avatarFor } from "../ui/identity";

interface Props {
  /** The viewer's name, so their own row can be picked out of the table. */
  name: string;
  onBack: () => void;
}

/**
 * The global table.
 *
 * Global in the literal sense — every wallet on the server, not this room and
 * not this session. Ranks come from the query rather than from anything
 * stored, because a rank changes whenever somebody else finishes a game.
 */
export function RankingsScreen({ name, onBack }: Props) {
  const [rows, setRows] = useState<Ranked[] | null>(null);
  const [failed, setFailed] = useState(false);
  const mine = name.trim().toLowerCase();

  useEffect(() => {
    let cancelled = false;
    fetchRankings(50)
      .then((r) => !cancelled && setRows(r))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="frame">
      <div className="homebar">
        <button type="button" className="ghost" onClick={onBack}>
          ← back
        </button>
        <span className="spacer" />
      </div>

      <div className="center">
        <h1 className="pagetitle">Global Rankings</h1>
        <p className="muted">Ranked by coins earned, across every game ever played here.</p>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">🏆 top players</span>
          <span className="spacer" />
          {rows !== null && <span className="label">{rows.length} listed</span>}
        </div>

        {failed ? (
          <p className="muted">Couldn&apos;t load the rankings — is the server running?</p>
        ) : rows === null ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted">
            Nobody has finished a game yet. Play one and you are the leaderboard.
          </p>
        ) : (
          <ol className="board" aria-label="global rankings">
            {rows.map((row) => (
              <li
                key={row.wallet.name + row.rank}
                className={`row${row.wallet.name.toLowerCase() === mine ? " you" : ""}`}
              >
                <span className="rank">{row.rank}</span>
                <span className="face" aria-hidden="true">
                  {avatarFor(row.wallet.name)}
                </span>
                <span className="who">{row.wallet.name}</span>
                <span className="chip">{row.wallet.tier}</span>
                <span className="record">
                  {row.wallet.wins}W / {row.wallet.games_played}
                </span>
                <span className="score">{row.wallet.coins}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
