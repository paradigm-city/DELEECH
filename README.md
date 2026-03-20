# DELEECH

Anti-leecher plugin for **Nicotine+**.

DELEECH watches users when they queue uploads, checks whether they expose a minimum number of public shares, warns users who fall below the configured threshold, tracks repeated offenses in a local SQLite database, and can automatically ban and unban repeat offenders.



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
5. After an upload finishes, the plugin can:
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

A user is considered acceptable when both conditions are true:

- `num_files >= configured minimum`
- `num_folders >= configured minimum`

Default values:

- **60 files**
- **1 public folder**

### Suspicious-user heuristics

Even if a user appears to meet the minimum threshold, the plugin treats certain share counts as suspicious and forces a deeper check. In the current code, a user is flagged as suspicious when one of the following is true:

- exactly `1000 files / 50 folders`
- more than `2000 files per folder`
- file count is evenly divisible by folder count **and** by `50`
- file count is divisible by `100`

These heuristics are intended to catch obviously synthetic or stale stats.

### Warnings and repeat cycle

When a user is identified as a leecher, DELEECH can send a private message. That message supports placeholders such as:

- `%files%`
- `%folders%`
- `%leecher%`

Warnings are not necessarily sent after every single upload. The warning frequency is controlled by the `msg_repeat_after` parameter. The plugin uses an internal state machine such as `pending_leecher`, `processed_leecher<nn>`, `pending_ban`, and `check_before_ban` to control when the message is repeated.

### Automatic bans

If automatic banning is enabled, the plugin increments strikes and bans users after they exceed the configured warning threshold.

Default values:

- `auto_ban_leechers = True`
- `auto_ban_after = 3`

The current implementation bans when:

- the strike count grows beyond `auto_ban_after`, or
- the user's downloaded volume exceeds `leecher_quota_mb`. This is used to handle high volume leechers. 

Before the final ban in the normal warning path, DELEECH performs one more share verification request.

### Ban duration growth

Ban length escalates with the number of prior unbans using this formula:

```text
ban_days = int(10 ** (unban_count / 5))
```

That means repeat offenders are banned for increasingly longer periods.

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
| `debug_log` | `False` | Enables debug logging |

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
- `upload_finished_notification()`
- `user_stats_notification()`
- `user_status_notification()`
- `core.users.request_user_stats()`
- `core.userbrowse.request_user_shares()`
- `core.network_filter.ban_user()`
- `core.network_filter.unban_user()`

So this is not a standalone Python utility; it is a Nicotine+ plugin that depends on the Nicotine+ plugin/runtime environment.

---

## Known limitations and code-level observations

These points come directly from the current implementation and are worth knowing before deployment:

1.**Buddies are exempted** from enforcement even if they do not meet the configured share thresholds.
2. **Warning/banning is post-transfer oriented.** In the common path, the plugin lets the current transfer complete, then warns or escalates afterward.
3. **Suspicious-user heuristics are opinionated.** Some legitimate users with round-number statistics may be forced into additional verification.
4. **The plugin depends on filesystem access to the transferred file path** in order to calculate the completed upload size.

---

## Metadata

From `PLUGININFO`:

- **Name:** `DELEECH`
- **Description:** `Leech detector SQLite version`
- **Version:** `2026-03-20r00`
- **Author:** `Paradigm_city`

---

## License

The source files declare:

- `SPDX-License-Identifier: GPL-3.0-or-later`
