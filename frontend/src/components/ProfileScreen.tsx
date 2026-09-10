import { emptyWallet, type Wallet } from "../net/menu";
import { avatarFor } from "../ui/identity";

interface Props {
  name: string;
  wallet: Wallet | null;
  onBack: () => void;
}

function winRate(wallet: Wallet): string {
  if (wallet.games_played === 0) return "—";
  return `${Math.round((wallet.wins / wallet.games_played) * 100)}%`;
}

/**
 * One player's standing.
 *
 * A name with no wallet renders as a wallet full of zeroes rather than an
 * error, because to the person looking at it those are the same thing: they
 * have not finished a game yet.
 */
export function ProfileScreen({ name, wallet, onBack }: Props) {
  const w = wallet ?? emptyWallet(name);
  const toNext = w.coins_to_next;

  return (
    <div className="frame">
      <div className="homebar">
        <button type="button" className="ghost" onClick={onBack}>
          ← back
        </button>
        <span className="spacer" />
        <span className="coin">
          <span aria-hidden="true">🪙</span>
          <span className="sr-only">your coins: </span>
          {w.coins}
        </span>
      </div>

      <div className="profilehead">
        <span className="bigface" aria-hidden="true">
          {avatarFor(name)}
        </span>
        <div>
          <h1 className="pagetitle">{name || "Nameless"}</h1>
          <span className="tierchip">{w.tier}</span>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">📈 record</span>
        </div>
        <div className="statgrid">
          <Stat label="coins" value={w.coins} tone="gold" />
          <Stat label="games played" value={w.games_played} />
          <Stat label="wins" value={w.wins} />
          <Stat label="win rate" value={winRate(w)} />
          <Stat label="best game" value={w.best_score} />
          <Stat label="lifetime points" value={w.lifetime_score} />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">🎖️ next rank</span>
        </div>
        {w.next_tier === null || toNext === null ? (
          <p className="muted">
            {w.tier} is the top of the ladder. There is nothing above you.
          </p>
        ) : (
          <>
            <p className="muted">
              <b>{toNext}</b> more coins to reach <b>{w.next_tier}</b>.
            </p>
            <div
              className="meter"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={w.coins + toNext}
              aria-valuenow={w.coins}
            >
              <i style={{ width: `${(w.coins / (w.coins + toNext)) * 100}%` }} />
            </div>
          </>
        )}
      </div>

      <p className="muted footnote">
        Coins are earned by finishing games: 10 for playing, 1 per 20 points, and a
        podium bonus. Wallets are keyed by name — v1 has no accounts, so anyone
        playing under your name plays into your wallet.
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "gold";
}) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <b className={tone === "gold" ? "gold" : undefined}>{value}</b>
    </div>
  );
}
