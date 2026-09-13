# Music Legends — Refactor Plan

> Drop this at `docs/REFACTOR_PLAN.md`. It is the source of truth for the current
> refactor. Re-read it whenever context clears.

---

## Context for the implementing agent

Music Legends is a collectible music-card game with two surfaces: a Discord bot and a
Telegram Mini App. Cards represent songs and artists. Card power derives from live
YouTube view counts.

The project stalled because the battle system has no player decisions — it compares two
power values and rolls for a crit. That is a design gap, not a bug.

**Strategic decision: Telegram is the primary surface. Discord is frozen at bugfix-only.**
Do not add features to Discord. Do not chase parity between surfaces. The parity treadmill
is what stalled this project.

Work the phases in order. Each phase has acceptance criteria. Do not advance until they pass.

---

## Phase 0 — Repo transfer (manual, human-only)  ✅ DONE 2026-09-13

> Repo pushed to `KJohnson-700/Music-Legends`. Railway kept; deploys now run from the local
> checkout via `railway up --service <name> --ci` because the Railway↔GitHub link is tied to
> the old GitHub account. Both TMA services deployed and healthy. No Discord service exists
> on Railway (deleted months ago) — consistent with Discord being bugfix-only.

Do this before any code changes. Do not refactor while services are being repointed.

1. GitHub → repo Settings → Transfer ownership from `samuraifrenchienft` to `kenjohnson`.
   The new owner gets a confirmation email; the invite expires in one day. The target
   account must not already have a repo with that name.
2. Install the Railway GitHub App on the `kenjohnson` account with access to the repo. Do
   this from whichever GitHub account your Railway login is linked to — each Railway user
   has their own App installation controlling repo visibility.
3. In Railway, for **each service**: Settings → Source Repo → disconnect → reconnect to the
   new repo and branch. This re-registers the service-level webhook, which is separate from
   the account-level integration. **Skipping this means pushes silently stop triggering
   deploys.**
4. Push an empty commit. Confirm a deploy triggers.
5. Check `.github/` workflows. Verify Actions secrets survived; re-add if not.
6. Remove `samuraifrenchienft` from collaborators — GitHub auto-adds the original owner.
7. Scan history for committed secrets. `env-example.txt` is in the repo; confirm it never
   held real values.

Keep the same Railway project. Creating a new one means re-entering every env var and
dumping/restoring Postgres. Keeping it preserves `RAILWAY_PUBLIC_DOMAIN`, so there is no
Telegram webhook re-registration, no BotFather URL change, and no Stripe webhook update.

**Acceptance:** an empty commit to the new repo triggers a successful Railway deploy; bot
and Mini App both respond.

---

## Phase 1 — Decouple the battle engine  ✅ DONE 2026-09-13

> Landed as `core/battle/` (config, types, resolver, manager) + `adapters/discord_battle.py`,
> `adapters/tma_battle.py`. `battle_engine.py` is now a shim that never imports discord.
> Legacy `BattleEngine` class deleted; all callers use `resolve_match()`.
> Seeded tests: `tests/test_resolver.py`. Verified: `core.battle.resolver` and
> `tma.api.routers.battle` import with discord.py blocked.

**Problem:** `battle_engine.py` does `import discord` at module level, and
`create_battle_embed()` returns a `discord.Embed`. The Telegram FastAPI backend imports this
module, so it depends on discord.py at runtime and would crash without it.

**Target layout**

```
core/battle/
  types.py       # BattleCard, PlayerState, MatchState, BattleStatus
  config.py      # BattleWagerConfig, GENRE_RING, multipliers
  resolver.py    # pure functions: resolve_round(), resolve_match()
  manager.py     # BattleManager
adapters/
  discord_battle.py   # create_battle_embed, BattleHistory embeds
  tma_battle.py       # dict serialization for /api/battle/*
```

**Hard rule for `core/`:** no `import discord`, no `import fastapi`, no I/O. Pure logic only.

**Steps**

1. Move the four state classes (`BattleCard`, `PlayerState`, `MatchState`, `BattleStatus`)
   to `core/battle/types.py` unchanged. Verify the bot still boots.
2. Move `execute_battle` to `resolver.py` as a module-level function. It must take an
   explicit `random.Random(seed)` parameter rather than calling the global `random` module.
   This enables deterministic tests and match replay.
3. Move `create_battle_embed` and `BattleHistory.get_stats_embed` to
   `adapters/discord_battle.py`. This becomes the only file in the battle path importing
   discord.
4. Delete the legacy `BattleEngine` class once `cogs/battle_commands.py` calls the resolver.
   Two parallel battle systems currently coexist; only `MatchState` survives.

**Acceptance:** `python -c "import core.battle.resolver"` succeeds in an environment with
discord.py uninstalled. Existing battle commands still work in Discord.

---

## Phase 2 — Redis-backed match state

**Problem:** `BattleManager` holds `active_matches` and `user_to_match` in in-memory dicts.
On Railway under uvicorn, matches vanish on restart and break across workers — a player can
accept a battle on one worker that does not exist on another. This is a production blocker.

`redis.conf` and `rq_queue/` already exist in the repo, so the dependency is available.

**Implementation**

- Keys: `match:{match_id}` and `user_match:{user_id}`
- TTL matching battle expiry; both keys must expire together
- Serialize `MatchState` to JSON. No pickle.

**Acceptance:** start a battle, restart the service, the battle is still resolvable. Run two
workers locally and confirm a match created on one is visible to the other.

---

## Phase 3 — Log-compress power

**This unblocks Phase 4. Do it first.**

**Problem:** power derives from raw `view_count`. Test fixtures show Drake at 1.2B and
Taylor Swift at 800M, but a real catalog spans 2B down to 5M — a 400x gap. No
percentage-based multiplier can overcome that, so any genre or ability system would be inert
decoration.

Check `card_stats.py` for the current derivation.

**Formula**

```python
power = round(180 * math.log10(max(view_count, 10_000)) - 620)
```

Maps 10M views to roughly 640 and 2B to roughly 1030. Gaps become meaningful but not
decisive.

**Acceptance:** across the real card table, the ratio between the strongest and weakest card
is under 2x, and a 35% multiplier can flip a matchup between cards two tiers apart.

---

## Phase 4 — Make battle a game

### 4a. Genre data layer

`ArtistCard` currently has no genre field. Add a `genre_family` enum column, resolved **once
at card creation**, never at battle time.

Resolution chain: `lastfm_integration.py` top tags (track first, then artist) →
`audiodb_integration.py` `strGenre` → `NEUTRAL` fallback.

Static tag-to-family dict in `cards_config.py`. Normalize to lowercase and strip hyphens —
Last.fm tags are messy: "hip hop", "hip-hop", "rap", and "trap" all need to land on the same
family.

Alembic migration to backfill existing cards, run as a batch job through `rq_queue` to
respect Last.fm rate limits.

### 4b. The genre ring

Five families. Each beats one, loses to one, neutral to two.

| Family | Beats | Loses to |
|---|---|---|
| HIP_HOP | POP | SOUL |
| POP | ROCK | HIP_HOP |
| ROCK | ELECTRONIC | POP |
| ELECTRONIC | SOUL | ROCK |
| SOUL | HIP_HOP | ELECTRONIC |

`NEUTRAL` and mirror matchups are neutral both ways.

Multipliers: counter `1.35x`, countered `0.85x`, neutral `1.0x`. Roughly a 1.6x effective
swing.

### 4c. Lineups and rounds

Three-card ordered lineup, best of three rounds, revealed one at a time. Round N faces slot N.

**Constraint: maximum two cards from the same family.** This is the rule that converts
collecting into deck-building.

Genre multiplier applies per round. Crit stays, rolled per round. First to two wins. A 1-1-1
result with a tie round resolves on total modified power.

The ordering guess under incomplete information is the actual game.

### 4d. Abilities

One ability per battle, chosen at lineup time, single use.

- **Swap** — after round 1, exchange slots 2 and 3
- **Amp** — +20% to one card, declared before its reveal
- **Scout** — reveal the family, not the card, of the opponent's next slot

### 4e. Momentum bonus

Store weekly `view_delta` per card, refreshed via the existing `scheduler.py`. The top 10%
of movers that week get +10% power.

This is the moat. The meta shifts on its own when a song blows up, and it gives players a
reason to open the app daily. Nothing else on Telegram has this.

### 4f. Locked resolution order

```
base → momentum → genre → ability → crit
```

Persist the per-round breakdown in the match record. Needed for dispute handling and to
drive the reveal animation.

**Acceptance:** `tests/test_resolver.py` with a fixed seed asserts genre multipliers, crit
application, round resolution, and the lineup family constraint. A player with weaker cards
but correct genre reads beats a player with stronger cards and poor ordering at a
meaningfully better than chance rate.

---

## Phase 4.5 — Crafting (duplicate fusion)

**Why:** Telegram's own gift economy runs two mechanics — Upgrade (spend Stars for a
guaranteed collectible) and Crafting (combine up to four gifts for a probabilistic
higher-rarity result, launched Feb 2026). Users on this platform already understand fusion.
Caps has 14M users doing collect-and-trade with no battle system at all, and its retention
hook is staking gifts for in-app currency.

Crafting is the mechanic worth adopting. It solves two problems at once:

1. **Duplicates currently have no use.** Every duplicate pulled from a pack is dead weight
   that makes the next pack feel worse.
2. **There is no Stars sink outside pack purchases.** Crafting adds a second one that does
   not require printing new supply.

### Mechanic

Combine **four cards of the same rarity** for a probabilistic roll at the next rarity up.
Cards are consumed on craft, win or lose.

- Base success rate scales inversely with target rarity.
- A **Stars-paid boost** raises the success rate for that single craft. This is the sink.
- On failure, return one card of the *input* rarity — a floor, so crafting never feels like
  pure theft.
- Enforce the same-rarity check server-side. Never trust a client-submitted craft payload.

### Interaction with the genre system

Crafting must respect `genre_family`. Two options — pick one and stay consistent:

- **Inherit:** if all four inputs share a family, the output is guaranteed that family.
  Mixed inputs give a random family. This makes crafting a deck-building tool and pairs
  directly with the Phase 4c two-per-family lineup constraint.
- **Random:** output family is always random. Simpler, but wastes the synergy.

Recommend **Inherit**. It gives players a reason to hoard duplicates of a specific family
rather than crafting whatever is lying around.

### Interaction with supply

`season_supply.py` and `season_1_supply.json` already exist. Crafting **removes** cards from
circulation and mints a rarer one, so it must be reconciled against season supply caps or it
will quietly inflate the top of the rarity curve. Decide whether crafted cards draw from the
season pool or from a separate crafted allocation.

### Not doing

**Staking.** Caps stakes gifts for idle currency. That is a yield mechanic for a collection
app with no gameplay. Music Legends already gives cards a use — battling. Do not add a
mechanic that rewards *not* playing.

**Acceptance:** four same-rarity cards can be crafted; inputs are consumed atomically in a
transaction; failure returns the floor card; the Stars boost is charged before the roll and
refunded if the transaction fails; season supply accounting stays balanced.

---

## Phase 5 — Telegram Stars

**Problem:** `stripe_payments.py` exists; Stars do not. Under Apple and Google policy,
digital goods in a Mini App must use Stars — fiat cannot be charged directly. The
marketplace cannot ship as built.

Replace Stripe with Stars for all Mini App purchases, starting with
`/api/packs/{id}/purchase`. **Keep Stripe for Discord.** Both rails coexist; they do not
conflict.

Economics: 1 Star is roughly 1.3–1.5 cents. A 1,000-Star purchase costs the user about $20
and nets the developer about $13.30. Stars convert to TON via Fragment.

**Server-owner revenue share must be defined on net, not gross.** At 10% of net on a $20
purchase you keep about $12.15, versus about $11.50 on gross. Do not let the Stars haircut
come out of your side alone.

**Acceptance:** a pack purchase completes end to end in Telegram using Stars, credits the
correct cards, and records the server-owner cut against net revenue.

---

## Phase 6 — Polish

- Round-by-round reveal animation in the Mini App. Do not show instant results — the reveal
  is the drama, and the per-round breakdown from 4f already provides the data.
- Daily claim streak with escalating rewards.

---

## Standing constraints

- Discord: bugfix only. Leave `cogs/vip_commands.py` unloaded — it exists but is not loaded
  in `main.py`, and that is intentional.
- No new `import discord` anywhere under `core/`.
- Every resolver change needs a seeded test before merge.
