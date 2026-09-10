# DELEECH

Anti-leecher plugin for **Nicotine+**.

DELEECH watches users when they queue uploads, checks whether they expose a minimum number of public shares, warns users who fall below the configured threshold, tracks repeated offenses in a local SQLite database, and can automatically ban and unban repeat offenders.

DELEECH is an extension of the Leech Detector plugin.

This README is based on the current code in `DELEECH/__init__.py` and `DELEECH/PLUGININFO`.

---

## Why this exists

Sharing is the foundation, not an optional extra.
A file-sharing network survives only when enough users actually share.

Risk should not be one-sided.
Uploaders bear the bandwidth load, availability burden, and often the greater exposure. It is reasonable to expect downloaders to contribute in return.

Fairness needs enforcement.
Without consequences, “share if you feel like it” quickly becomes “take without giving back.” DELEECH exists to promote this kind of reciprocity.

The goal is not punishment. The goal is to protect the people who keep the network alive, discourage exploitative behavior, and preserve a culture of mutual exchange.

## What it does

The plugin hooks into Nicotine+ upload and user-stat notifications and applies a simple enforcement workflow:

1. A user queues an upload.
2. DELEECH requests or refreshes that user's share statistics.
3. The user is accepted if they meet the configured minimum number of shared files **and** shared folders.
4. If the user does not meet the threshold, DELEECH marks them as a leecher candidate.
5. When an upload starts, the plugin can:
   - send a warning message,
   - increment a strike counter,
   - repeat warnings after a configurable number of uploads,
   - ban the user after enough warnings,
   - or ban immediately when the leecher upload quota is exceeded.
6. Ban history and strike state are persisted in a local SQLite database.

The plugin also attempts to verify suspicious share counts by requesting the user's actual shares from the peer before taking final action.

---

## Core behavior

### Minimum share requirements

A user is considered acceptable when all configured conditions are true:

- `num_files >= configured minimum` (default: 60)
- `num_folders >= configured minimum` (default: 1)
- `shared_size_mb >= min_shared_mb` (default: 50 MB)
- `avg_file_size_kb >= min_avg_file_kb` (default: 500 KB)

Default values:

- **60 files**
- **1 public folder**
- **50 MB total shared size**
- **500 KB average file size**

### Suspicious-user heuristics

Even if a user appears to meet the minimum threshold according to Soulseek server stats, the plugin treats certain patterns as suspicious and triggers a deep peer share verification (`core.userbrowse.request_user_shares`).

Matching a suspicious pattern on server stats is **only a suspicion trigger**: it forces direct inspection of the peer's actual shares and does **not** convict or ban the user. If the peer's actual shares meet the file, folder, and size requirements, they are cleared.

The plugin checks against curated known scraper/bot fake share profiles:

- `(1000, 50)` — Classic 20:1 Soulseek bot/scraper hardcoded profile
- `(2000, 100)` — 20:1 scaled bot profile
- `(1500, 75)` — 20:1 scaled bot profile
- `(500, 25)` — 20:1 minimal scraper profile
- `(3000, 150)` — 20:1 extended scraper profile
- `(100, 50)` — Community-reported 2:1 throwaway Spotify bot pattern
- `(500, 50)` — 10:1 round profile
- `(250, 25)` — 10:1 round profile
- `(100, 10)` — 10:1 minimal fake share profile
- More folders than files when `num_folders > 5` (empty folder dummy generation)
- More than `2000 files per folder` (unusually flat single-folder dump)

### Warnings and repeat cycle

When a user is identified as a leecher, DELEECH can send a private message. That message supports placeholders such as:

- `%files%`
- `%folders%`
- `%leecher%`

Warnings are sent when an upload starts. The warning frequency is controlled by the `msg_repeat_after` parameter. The plugin uses an internal state machine such as `pending_leecher`, `processed_leecher<nn>`, `pending_ban`, and `check_before_ban` to control when the message is repeated.

### Automatic bans

If automatic banning is enabled, the plugin increments strikes and bans users after they exceed the configured warning threshold.

Default values:

- `auto_ban_leechers = True`
- `auto_ban_after = 3`

The current implementation bans when:

- the strike count grows beyond `auto_ban_after`, or
- the user's downloaded volume exceeds `leecher_quota_mb`. This is used to handle high volume leechers. 

Before the final ban in the normal warning path, DELEECH performs one more share verification request.

### Ban duration growth (Fibonacci & Exponential)

Ban length escalates with recidivism (`unban_count`). By default, DELEECH uses a **Fibonacci progression**:

| Offense | Ban Duration (Fibonacci) |
|---|---|
| 1st Ban | **1 day** |
| 2nd Ban | **2 days** |
| 3rd Ban | **3 days** |
| 4th Ban | **5 days** |
| 5th Ban | **8 days** |
| 6th Ban | **13 days** |
| 7th Ban | **21 days** |
| 8th Ban | **34 days** |
| 9th Ban | **55 days** |
| 10th Ban | **89 days** |

The progression mode is configurable via `ban_progression` (`fibonacci` or `exponential`). In exponential mode:

```text
ban_days = int(10 ** (unban_count / 5))
```

### Automatic unban

When a banned user appears again, the plugin checks the stored `ban_end_date`. If the ban has expired, it automatically unbans the user and clears the active strike state.

### Upload quota

The plugin tracks how many megabytes a leecher has downloaded in total and can ban once the configured quota is exceeded.

Default value:

- `leecher_quota_mb = 200`

---

## Configuration

The plugin defines the following user-facing settings:

| Setting | Default | Purpose |
|---|---:|---|
| `message` | warning text | Private message sent to leechers |
| `ban_message` | short ban notice | Message sent after ban |
| `open_private_chat` | `True` | Opens a chat tab when messaging |
| `msg_repeat_after` | `5` | Number of uploads before warning is repeated |
| `auto_ban_leechers` | `True` | Enables automatic banning |
| `auto_ban_after` | `3` | Number of warnings before ban |
| `auto_unban_leechers` | `True` | Exposed in settings metadata |
| `leecher_quota_mb` | `200` | Allowed downloaded volume before forced ban |
| `num_files` | `60` | Minimum required shared files |
| `num_folders` | `1` | Minimum required shared public folders |
| `min_shared_mb` | `50` | Minimum total shared data in MB (0 to disable) |
| `min_avg_file_kb` | `500` | Minimum average file size in KB to catch dummy files |
| `ban_progression` | `fibonacci` | Ban escalation formula (`fibonacci` or `exponential`) |
| `show_ui_tab` | `True` | Displays dedicated DELEECH Monitor tab in main window |
| `debug_log` | `False` | Enables debug logging |

---

## DELEECH Monitor Tab (UI Transparency)

When `show_ui_tab` is enabled, DELEECH embeds a dedicated tab directly into the Nicotine+ main window.

### Features
- **Live User Monitor**: Visual `TreeView` displaying tracked leechers, current status (`BANNED`, `Pending Ban`, `Warned`, `Auditing Shares`), active/lifetime strikes, uploaded data vs. quota (`mb_uploaded / quota_mb`), ban expiration date, unban count, and last strike timestamp.
- **Search & Filter**: Real-time username filtering via a search entry and status dropdown filter (`All`, `BANNED`, `Active Leechers`).
- **Interactive Management Actions**:
  - **Refresh**: Re-queries the SQLite database and updates the view instantly.
  - **Reset Strikes**: Clears active strikes and warnings for the selected user, giving them a fresh start.
  - **Unban Selected**: Immediately lifts the network filter ban for the selected user and resets ban expiry.
  - **Browse Shares**: Opens Nicotine+'s native share browser for the selected user to manually inspect their files.
- **Headless & Cross-GTK Resilience**: Dispatches UI updates via `GLib.idle_add` for thread safety, safely degrades when running in CLI/headless mode, and adapts to both GTK 3 and GTK 4 container models.

---

## SQLite persistence

DELEECH stores its state in a local SQLite database named:

```text
deleech.db
```

The file is created in the Nicotine+ data folder returned by `config.get_user_folders()`.

### Table: `strikes`

The plugin creates a single table:

- `leecher` — username, unique
- `strikes` — current active strike count
- `strikedate` — current strike timestamp
- `is_banned` — whether the user is currently considered banned
- `strikes_total` — lifetime strike count
- `laststrikedate` — latest strike timestamp
- `unban_count` — number of unbans performed
- `unban_date` — latest unban timestamp
- `ban_end_date` — calculated ban expiry
- `mb_uploaded` — total MB uploaded to this user during leecher tracking
- `last_state` — last internal workflow state

The plugin also resets stale strikes if the last strike is older than **90 days**.

---

## Installation

### Option 1: manual install

1. Download or clone this repository.
2. Copy the `DELEECH` directory into your Nicotine+ plugins directory.
3. Restart Nicotine+ or reload plugins.
4. Enable **DELEECH** in the Nicotine+ plugin manager.
5. Review the default thresholds before using it on a live account.

### Expected plugin layout

```text
DELEECH/
├── README.md
└── DELEECH/
    ├── __init__.py
    └── PLUGININFO
```

---

## Runtime integration points

The plugin is built around Nicotine+ plugin callbacks and APIs such as:

- `loaded_notification()`
- `upload_queued_notification()`
- `upload_started_notification()`
- `user_stats_notification()`
- `user_status_notification()`
- `core.users.request_user_stats()`
- `core.userbrowse.request_user_shares()`
- `core.network_filter.ban_user()`
- `core.network_filter.unban_user()`

So this is not a standalone Python utility; it is a Nicotine+ plugin that depends on the Nicotine+ plugin/runtime environment.

---

## Known limitations

1. **Buddies are exempted** from enforcement even if they do not meet the configured share thresholds.
2. **Interception occurs at transfer start.** Warnings, strikes, and bans take effect when an upload starts (`upload_started_notification`), stopping repeat offenders before they download files.
3. **Suspicious-user heuristics are opinionated.** Some legitimate users with round-number statistics may be forced into additional verification.
4. **The plugin depends on filesystem access to the transferred file path** in order to calculate the completed upload size.

---

## Metadata

From `PLUGININFO`:

- **Name:** `DELEECH`
- **Description:** `Leech detector SQLite version`
- **Version:** `2026-09-10r00`
- **Author:** `Paradigm_city`

---

## License

The source files declare:

- `SPDX-License-Identifier: GPL-3.0-or-later`
