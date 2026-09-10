# SPDX-FileCopyrightText: 2020-2025 Nicotine+ Contributors
# SPDX-FileCopyrightText: 2011 quinox <quinox@users.sf.net>
# SPDX-License-Identifier: GPL-3.0-or-later

# Filename: __init__.py
# Author: paradigm_city
# Description: DELEECH plugin for nicotine+
# Version: 0.7
# Schema version: 3

from pynicotine.pluginsystem import BasePlugin
from pynicotine.config import config
from datetime import datetime, timedelta
import sqlite3
import os
import re

class Plugin(BasePlugin):

    PLACEHOLDERS = {
        "%files%": "num_files",
        "%folders%": "num_folders"
    }

    # Curated set of known scraper / bot / fake-share patterns (files, folders)
    # Matching on server stats triggers deep peer share investigation without immediate conviction.
    SUSPICIOUS_PATTERNS = {
        (1000, 50),   # Classic 20:1 Soulseek bot/scraper hardcoded profile
        (2000, 100),  # 20:1 scaled bot profile
        (1500, 75),   # 20:1 scaled bot profile
        (500, 25),    # 20:1 minimal scraper profile
        (3000, 150),  # 20:1 extended scraper profile
        (100, 50),    # Community-reported 2:1 throwaway Spotify bot pattern
        (500, 50),    # 10:1 round profile
        (250, 25),    # 10:1 round profile
        (100, 10),    # 10:1 minimal fake share profile
    }

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.settings = {
            "message": "Hi, DELEECH plugin here. Peers are requested to share %files% files or more. Please consider sharing more files if you would like to download from me again. Thanks.",
            "ban_message": "Hi, DELEECH plugin here. You have been automatically banned.",
            "open_private_chat": True,
            "msg_repeat_after": 5,
            "auto_ban_leechers": True,
            "auto_ban_after": 3,
            "auto_unban_leechers": True,
            "leecher_quota_mb": 200,
            "num_files": 60,
            "num_folders": 1,
            "min_shared_mb": 50,
            "min_avg_file_kb": 500,
            "ban_progression": "fibonacci",
            "sort_column": "strikes",
            "sort_order": "descending",
            "show_ui_tab": True,
            "debug_log": False,
            "schema_version": 1,
            "banned_name_patterns": "^LeecherNameHere"
        }

        self.metasettings = {
            "message": {
                "description": ("Private chat message to send to leechers. Each line is sent as a separate message, "
                                "too many message lines may get you temporarily banned for spam."),
                "type": "textview"
            },
            "ban_message": {
                "description": ("Private chat message to send to leechers after they are banned."),
                "type": "textview"
            },
            "open_private_chat": {
                "description": "Open chat tabs when sending private messages to leechers",
                "type": "bool"
            },
            "msg_repeat_after": {
                "description": "Number of uploads before message is repeated:",
                "type": "int", "minimum": 3
            },
            "auto_ban_after": {
                "description": "Number of warnings before leecher is banned:",
                "type": "int", "minimum": 1
            },
            "leecher_quota_mb": {
                "description": "MB upload allowed before leecher is banned:",
                "type": "int", "minimum": 1, "stepsize": 25
            },
            "auto_ban_leechers": {
                "description": "Automatically ban leechers",
                "type": "bool"
            },
            "auto_unban_leechers": {
                "description": "Auto unban leechers",
                "type": "bool"
            },
            "num_files": {
                "description": "Minimum number of shared files required:",
                "type": "int", "minimum": 3
            },
            "num_folders": {
                "description": "Minimum number of shared folders required:",
                "type": "int", "minimum": 1
            },
            "min_shared_mb": {
                "description": "Minimum total shared data in MB (0 to disable):",
                "type": "int", "minimum": 0, "stepsize": 25
            },
            "min_avg_file_kb": {
                "description": "Minimum average file size in KB to detect dummy files (0 to disable):",
                "type": "int", "minimum": 0, "stepsize": 100
            },
            "ban_progression": {
                "description": "Ban duration escalation formula:",
                "type": "dropdown",
                "options": ["fibonacci", "exponential"]
            },
            "show_ui_tab": {
                "description": "Show DELEECH monitor tab in main window",
                "type": "bool"
            },
            "debug_log": {
                "description": "Debug logging",
                "type": "bool"
            },
            "banned_name_patterns": {
                "description": (
                    "Banned username patterns (one regex per line). "
                    "Matching users are immediately banned on first queue attempt, no warnings. "
                    "Example: ^aurral_ matches any name starting with aurral_"
                ),
                "type": "textview"
            }
        }

        self.probed_users = {}
        self._banned_patterns = []

        # UI Transparency state
        self.ui_page = None
        self.treeview = None
        self.stats_label = None
        self.filter_entry = None
        self._filter_text = ""

        config_folder_path, data_folder_path = config.get_user_folders()

        # database
        database_path = os.path.join(data_folder_path, "deleech.db")
        self.log("database: %s", database_path)
        self.conn = sqlite3.connect(database_path)
        self.csr = self.conn.cursor()

    def __del__(self):
        self._teardown_ui()
        try:
            self.log("cursor closing...")
            self.csr.close()
        except Exception:
            pass
        try:
            self.log("connection closing...")
            self.conn.close()
        except Exception:
            pass
        self.log("cleanup done")

    def _write_ui_log(self, msg):
        try:
            log_path = os.path.join(os.path.dirname(__file__), "ui_debug.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def init(self):
        self._write_ui_log("init() called")
        if self.settings.get("show_ui_tab", True):
            self._init_ui()

    def disable(self):
        self._write_ui_log("disable() called")
        self._teardown_ui()

    def unloaded_notification(self):
        self._write_ui_log("unloaded_notification() called")
        self._teardown_ui()

    def dbinit(self):
        self.log_debug("init db...")
        sql = "CREATE TABLE IF NOT EXISTS strikes(" \
              "leecher TEXT NOT NULL UNIQUE, " \
              "strikes INTEGER, " \
              "strikedate DATETIME, " \
              "is_banned int(1) default 0, " \
              "strikes_total integer, " \
              "laststrikedate datetime, " \
              "unban_count int default 0, " \
              "unban_date datetime, " \
              "ban_end_date datetime" \
              ")"
        self.csr.execute(sql)
        self.conn.commit()

        # maintain database schema
        if self.settings["schema_version"] < 2:
            self.log_debug("update schema to version 2")
            for sql in (
                "alter table strikes add column mb_uploaded real default 0",
                "alter table strikes add column last_state TEXT"
            ):
                try:
                    self.csr.execute(sql)
                    self.conn.commit()
                except sqlite3.OperationalError:
                    pass  # column already exists, ignore
            self.settings["schema_version"] = 2

        if self.settings["schema_version"] < 3:
            self.log_debug("update schema to version 3")
            # no changes to schema
            self.settings["schema_version"] = 3

    def log_debug(self, msg, msg_args=None):
        if self.settings["debug_log"]:
            super().log(msg, msg_args)

    def loaded_notification(self):

        min_num_files = self.metasettings["num_files"]["minimum"]
        min_num_folders = self.metasettings["num_folders"]["minimum"]

        if self.settings["num_files"] < min_num_files:
            self.settings["num_files"] = min_num_files

        if self.settings["num_folders"] < min_num_folders:
            self.settings["num_folders"] = min_num_folders

        self.log("Require users to share a minimum of %d files in %d shared public folder(s).",
                 (self.settings["num_files"], self.settings["num_folders"]))
        if self.settings["auto_ban_leechers"]:
            self.log("Leechers will be banned after %s warnings",
                     self.settings["auto_ban_after"])

        self.dbinit()
        self._compile_banned_patterns()

        self._write_ui_log(f"loaded_notification() called, show_ui_tab={self.settings.get('show_ui_tab', True)}")
        if self.settings.get("show_ui_tab", True):
            self._init_ui()

    def _compile_banned_patterns(self):
        self._banned_patterns = []
        raw = self.settings.get("banned_name_patterns", "")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self._banned_patterns.append(re.compile(line, re.IGNORECASE))
                self.log_debug("Loaded ban pattern: %s", line)
            except re.error as e:
                self.log("Invalid regex pattern '%s': %s", (line, e))
        self.log("Loaded %d banned name pattern(s).", len(self._banned_patterns))

    def _is_name_banned(self, user):
        for pattern in self._banned_patterns:
            if pattern.search(user):
                return pattern.pattern
        return None

    def on_auto_ban_leechers_toggled(self, switch, gparam):
        self.option_widgets["auto_ban_after"].set_sensitive(switch.get_active())

    def settings_changed_notification(self, before, after):
        if before.get("banned_name_patterns") != after.get("banned_name_patterns"):
            self._compile_banned_patterns()

        if before.get("show_ui_tab") != after.get("show_ui_tab"):
            if after.get("show_ui_tab"):
                self._init_ui()
            else:
                self._teardown_ui()

    def is_suspect_user(self, user, num_files, num_folders, shared_size=0, source="server"):
        """Evaluates whether share numbers warrant suspicion and deeper inspection."""
        try:
            num_files = int(num_files or 0)
        except (TypeError, ValueError):
            num_files = 0

        try:
            num_folders = int(num_folders or 0)
        except (TypeError, ValueError):
            num_folders = 0

        try:
            shared_size = int(shared_size or 0)
        except (TypeError, ValueError):
            shared_size = 0

        folders = max(num_folders, 1)
        files = max(num_files, 0)
        ratio = files / folders

        # Heuristic A: Dummy file / volume validation (applicable to both server and peer stats)
        if shared_size > 0:
            min_mb = self.settings.get("min_shared_mb", 0)
            if min_mb > 0 and (shared_size / (1024 * 1024)) < min_mb:
                self.log_debug("%s: suspect, total shared volume too small (%1.1f MB < %d MB)",
                               (user, shared_size / (1024 * 1024), min_mb))
                return True

            min_avg_kb = self.settings.get("min_avg_file_kb", 0)
            if min_avg_kb > 0 and files > 0:
                avg_kb = (shared_size / files) / 1024
                if avg_kb < min_avg_kb:
                    self.log_debug("%s: suspect, average file size too small (%1.1f KB < %d KB)",
                                   (user, avg_kb, min_avg_kb))
                    return True

        # Heuristics D & B: Server-side statistical suspicion triggers
        # Only used when source == "server" to force a peer share request; never directly convicts.
        if source == "server":
            # Match against curated suspicious patterns (e.g., 1000/50, 2000/100, 500/25, 100/50)
            if (num_files, num_folders) in self.SUSPICIOUS_PATTERNS:
                self.log_debug("%s: matches known suspicious pattern (%d files, %d folders)",
                               (user, num_files, num_folders))
                return True

            # More folders than files when folders > 5 indicates empty/dummy directories
            if folders > 5 and files < folders:
                self.log_debug("%s: suspect ratio, more folders than files (%d files in %d folders)",
                               (user, files, folders))
                return True

            # Flat single-folder dump anomaly
            if ratio > 2000:
                self.log_debug("%s: suspect ratio, dense single-folder dump (> 2000 files/folder)", user)
                return True

        return False

    @staticmethod
    def _fibonacci(n):
        """Returns the n-th Fibonacci penalty duration in days (n=1 -> 1, n=2 -> 2, n=3 -> 3, n=4 -> 5...)."""
        if n <= 1:
            return 1
        a, b = 1, 2
        for _ in range(2, n):
            a, b = b, a + b
        return b

    def bans_2_days(self, bans):
        mode = self.settings.get("ban_progression", "fibonacci")
        if mode == "exponential":
            days = 10 ** (float(bans) / 5)
            return int(days)
        # Default: Fibonacci progression
        return self._fibonacci(int(bans))

    def check_user(self, user, num_files, num_folders, shared_size=0, source="server"):
        if user not in self.probed_users:
            # We are not watching this user
            return

        if self.probed_users[user] == "okay":
            # User was already accepted previously, nothing to do
            return

        try:
            num_files = int(num_files or 0)
        except (TypeError, ValueError):
            num_files = 0

        try:
            num_folders = int(num_folders or 0)
        except (TypeError, ValueError):
            num_folders = 0

        try:
            shared_size = int(shared_size or 0)
        except (TypeError, ValueError):
            shared_size = 0

        source = str(source or "server")

        if (self.probed_users[user] == "requesting_shares" or self.probed_users[user] == "check_before_ban") and source != "peer":
            # Waiting for stats from peer, but received stats from server. Ignore.
            return

        size_desc = f"{shared_size / (1024 * 1024):1.1f} MB" if shared_size > 0 else "size unknown"
        self.log_debug("Checking user: %s (%s, %d files, %d folders, %s)",
                       (user, source, num_files, num_folders, size_desc))

        meets_counts = (num_files >= self.settings["num_files"] and num_folders >= self.settings["num_folders"])

        # Validate minimum size and average size if size data is present
        min_mb = self.settings.get("min_shared_mb", 0)
        min_avg_kb = self.settings.get("min_avg_file_kb", 0)
        meets_size = True
        if shared_size > 0:
            if min_mb > 0 and (shared_size / (1024 * 1024)) < min_mb:
                meets_size = False
            if min_avg_kb > 0 and num_files > 0 and ((shared_size / num_files) / 1024) < min_avg_kb:
                meets_size = False

        is_suspect = self.is_suspect_user(user, num_files, num_folders, shared_size, source)

        if source == "server":
            # On server stats: suspicion forces a deep peer share check without immediate conviction
            if is_suspect:
                is_user_accepted = False
                force_user_check = True
                self.log_debug("%s: suspect server stats, requesting peer shares to verify", user)
            else:
                is_user_accepted = (meets_counts and meets_size)
                force_user_check = False
        else:
            # On peer stats (source == "peer"): ground truth is verified directly.
            is_user_accepted = (meets_counts and meets_size and not is_suspect)
            force_user_check = False

        if is_user_accepted or user in self.core.buddies.users:
            self.probed_users[user] = "okay"
            self.unstrike_leecher(user)

            if is_user_accepted:
                self.log_debug("%s: okay, sharing %s files in %s folders (%s).",
                               (user, num_files, num_folders, source))
            else:
                self.log_debug("%s: buddy is sharing %s files in %s folders. Not complaining.",
                               (user, num_files, num_folders))
            self.trigger_ui_refresh()
            return
        else:
            # user was not accepted or buddy - check if a ban is pending
            self.log_debug("%s: NOT okay, sharing %s files in %s folders (%s).",
                           (user, num_files, num_folders, source))
            if self.probed_users[user] == "check_before_ban":
                self.log_debug("%s: arming a pending ban", user)
                self.probed_users[user] = "pending_ban"
                self.trigger_ui_refresh()
                return

        if not (self.probed_users[user].startswith("requesting") or self.probed_users[user] == "check_before_ban"):
            # We already dealt with the user this session
            return

        self.csr.execute("SELECT count(*) FROM strikes where leecher=?", [user])
        rows = self.csr.fetchall()
        if rows[0][0] > 0:
            # We already messaged the user in a previous session
            self.probed_users[user] = "processed_leecher01"
            self.trigger_ui_refresh()
            return

        if (num_files <= 0 or num_folders <= 0 or force_user_check) and self.probed_users[user] != "requesting_shares":
            self.log_debug("%s: verifying actual shares directly from peer…", user)
            self.probed_users[user] = "requesting_shares"
            self.core.userbrowse.request_user_shares(user)
            self.trigger_ui_refresh()
            return

        log_message = ("%s: leecher detected, sharing %s files in %s folders. Going to %s leecher when transfer starts.")

        if self.settings["message"]:
            notification_type = "message"
        else:
            notification_type = "log"

        self.probed_users[user] = "pending_leecher"
        self.log_debug(log_message, (user, num_files, num_folders, notification_type))
        self.trigger_ui_refresh()

    def upload_queued_notification(self, user, virtual_path, real_path):

        # Pattern ban: immediate ban, no warnings, no strike process
        matched_pattern = self._is_name_banned(user)
        if matched_pattern:
            if not self.core.network_filter.is_user_banned(user):
                self.log("%s: name matches banned pattern '%s', banning immediately.", (user, matched_pattern))
                self.core.network_filter.ban_user(user)
            return

        if user in self.probed_users:
            return

        # reset strikes if no recent events
        self.csr.execute("UPDATE strikes set strikes=0, strikedate=null, last_state=null where date(strikedate) < date('now', '-90 days') and leecher=?", [user])
        if self.csr.rowcount > 0:
            self.log_debug("%s: older strikes cleared", user)
        self.conn.commit()

        self.probed_users[user] = "requesting_stats"

        if user not in self.core.users.watched:
            # Transfer manager will request the stats from the server shortly
            return

        self.core.users.request_user_stats(user)

    def user_status_notification(self, user, status, privileged):
        if self.core.network_filter.is_user_banned(user): # did we ban this user?
            self.log_debug("%s: user was banned by us", user)
            self.csr.execute("update strikes set is_banned=1 where leecher=?", [user])
            self.conn.commit()
            self.csr.execute("SELECT leecher, strikes, strikedate, ban_end_date FROM strikes where strikedate is not null and leecher=?", [user])
            rows = self.csr.fetchall()
            for leecher, strikes, strikedate, ban_end_date in rows:
                self.log_debug("ban end date recorded: %s", [ban_end_date])
                if ban_end_date is None:
                    end_of_ban = datetime.strptime(strikedate, '%Y-%m-%d %H:%M:%S.%f') + timedelta(days=1)
                else:
                    try:
                        end_of_ban = datetime.strptime(ban_end_date, '%Y-%m-%d %H:%M:%S.%f')
                    except ValueError:
                        end_of_ban = datetime.strptime(ban_end_date, '%Y-%m-%d %H:%M:%S')
                self.log_debug("%s: user was banned after %d strikes, ban expires %s", (user, strikes, end_of_ban))
                if end_of_ban < datetime.now() and self.settings["auto_unban_leechers"]: # has the ban expired?
                    self.log_debug("%s: ban has expired, unban", user)
                    self.unstrike_leecher(leecher)
            self.trigger_ui_refresh()

    def user_stats_notification(self, user, stats):
        try:
            num_files = int(stats.get("files") or 0)
        except (TypeError, ValueError):
            num_files = 0

        try:
            num_folders = int(stats.get("dirs") or 0)
        except (TypeError, ValueError):
            num_folders = 0

        try:
            shared_size = int(stats.get("shared_size") or 0)
        except (TypeError, ValueError):
            shared_size = 0

        source = stats.get("source") or "server"

        self.check_user(
            user,
            num_files=num_files,
            num_folders=num_folders,
            shared_size=shared_size,
            source=source
        )

    def strike_leecher(self, user):
        if self.probed_users[user].startswith("processed_leecher"):
            self.log("%s: striking", user)

            self.csr.execute("insert or ignore into strikes(leecher, strikes, strikedate, laststrikedate, strikes_total) "
                             "values (?, ?, ?, ?, ?)",
                             [user, 0, datetime.now(), datetime.now(), 0])
            self.csr.execute("update strikes set "
                             "strikes=strikes+1, strikes_total=strikes_total+1, "
                             "strikedate=STRFTIME('%Y-%m-%d %H:%M:%f', 'now'), "
                             "laststrikedate=STRFTIME('%Y-%m-%d %H:%M:%f', 'now'), "
                             "last_state=? where leecher=?",
                             [self.probed_users[user], user])
            self.conn.commit()

        # we need to get these numbers regardless of whether warning level was raised
        self.csr.execute("SELECT leecher, strikes, unban_count FROM strikes where leecher=?", [user])
        rows = self.csr.fetchall()
        num_strikes = int(rows[0][1])
        unban_count = int(rows[0][2])

        # only output this message if warning level was actually raised before
        if self.probed_users[user].startswith("processed_leecher"):
            self.log_debug("%s: warning level set to %s", (user, num_strikes))

        if num_strikes >= self.settings["auto_ban_after"] or self.probed_users[user] == "leecher_exceeded_quota":
            if self.probed_users[user] == "pending_ban" or self.probed_users[user] == "leecher_exceeded_quota":
                self.log("%s: banning leecher after %s warnings", (user, num_strikes))
                ban_days = self.bans_2_days(unban_count + 1)
                self.log_debug("%s: unban_count: %d, ban_days: %d", (user, unban_count, ban_days))
                ban_end_date = datetime.now() + timedelta(days=ban_days)
                self.csr.execute("update strikes set is_banned=1, ban_end_date=?, last_state=? where leecher=?",
                                 [ban_end_date, self.probed_users[user], user])
                self.conn.commit()
                self.core.network_filter.ban_user(user)
                if self.settings["ban_message"]:
                    for line in self.settings["ban_message"].splitlines():
                        for placeholder, option_key in self.PLACEHOLDERS.items():
                            line = line.replace(placeholder, str(self.settings[option_key]))
                        line = line.replace("%leecher%", user)
                        self.send_private(user, line, show_ui=self.settings["open_private_chat"], switch_page=False)
            else:
                self.probed_users[user] = "check_before_ban"
                self.log_debug("%s: final request shares before ban", user)
                self.core.userbrowse.request_user_shares(user)

        self.trigger_ui_refresh()

    def unstrike_leecher(self, user):
        self.csr.execute("update strikes set strikes=0, strikedate=null, mb_uploaded=0, last_state=null where leecher=? and strikes > 0", [user])
        if self.csr.rowcount > 0:
            self.log_debug("%s: strikes reset", user)
        self.conn.commit()

        if self.core.network_filter.is_user_banned(user):
            self.log("%s: unban", user)
            self.core.network_filter.unban_user(user)
            self.csr.execute("update strikes set is_banned=0, unban_count=unban_count+1, unban_date=STRFTIME('%Y-%m-%d %H:%M:%f', 'now'), ban_end_date=NULL where leecher=?", [user])
            self.conn.commit()

        self.trigger_ui_refresh()

    def upload_started_notification(self, user, virtual_path, real_path):

        if user not in self.probed_users:
            return

        self.log_debug("%s: upload started - status %s", (user, self.probed_users[user]))

        if self.probed_users[user].startswith("processed_leecher"):
            file_size = os.path.getsize(real_path) / (1024 * 1024)
            self.log_debug("%s: downloading %1.1f MB.", (user, file_size))

            self.csr.execute("update strikes set mb_uploaded = mb_uploaded+?, last_state=? where leecher=?", [file_size, self.probed_users[user], user])
            self.conn.commit()

            self.csr.execute("SELECT mb_uploaded FROM strikes where leecher=?", [user])
            rows = self.csr.fetchall()
            mb_uploaded = int(rows[0][0])
            self.log_debug("%s: downloading %1.1f MB total so far.", (user, mb_uploaded))
            if mb_uploaded > self.settings["leecher_quota_mb"]:
                self.log_debug("%s: quota was exceeded", user)
                self.probed_users[user] = "leecher_exceeded_quota"
                self.strike_leecher(user)

        if self.probed_users[user] == "pending_leecher":

            self.probed_users[user] = "processed_leecher01"

            if self.settings["auto_ban_leechers"]:
                self.strike_leecher(user)

            if not self.settings["message"]:
                self.log_debug("%s: not msgd to leecher due to plugin settings.", user)
                self.trigger_ui_refresh()
                return

            for line in self.settings["message"].splitlines():
                for placeholder, option_key in self.PLACEHOLDERS.items():
                    line = line.replace(placeholder, str(self.settings[option_key]))
                line = line.replace("%leecher%", user)
                self.send_private(user, line, show_ui=self.settings["open_private_chat"], switch_page=False)

            self.log_debug("%s: msgd leecher", user)

        elif self.probed_users[user].startswith("processed_leecher"):
            self.log_debug("%s: %s", (user, self.probed_users[user]))
            llevel = int(self.probed_users[user][-2:])
            if llevel < self.settings["msg_repeat_after"] - 1:
                llevel += 1
                self.probed_users[user] = "processed_leecher{:02d}".format(llevel)
            else:
                self.probed_users[user] = "pending_leecher"

        elif self.probed_users[user].startswith("pending_ban"):
            self.strike_leecher(user)

        self.trigger_ui_refresh()

    # -------------------------------------------------------------------------
    # UI Transparency (GTK Monitor Tab & TreeView)
    # -------------------------------------------------------------------------

    def _init_ui(self):
        if self.ui_page is not None:
            return
        try:
            self._write_ui_log("_init_ui() scheduled via GLib.idle_add")
            from gi.repository import GLib
            GLib.idle_add(self._setup_ui)
        except Exception as e:
            self._write_ui_log(f"_init_ui() skipped/failed: {e}")
            self.log_debug("UI initialization skipped: %s", e)

    def _setup_ui(self):
        try:
            from gi.repository import GLib, Gio
            import pynicotine.gtkgui.application as app_module

            app = getattr(app_module, "_instance", None)
            if not app and hasattr(app_module, "Application"):
                app = getattr(app_module.Application, "_instance", None)
            if not app:
                try:
                    app = Gio.Application.get_default()
                except Exception:
                    pass

            self._write_ui_log(f"DEBUG app={app}, type={type(app).__name__ if app else None}")
            app_win = getattr(app, "window", None) if app else None
            self._write_ui_log(f"DEBUG app.window={app_win}, type={type(app_win).__name__ if app_win else None}")

            if app and hasattr(app, "get_windows"):
                wins = app.get_windows()
                self._write_ui_log(f"DEBUG get_windows count={len(wins)}")
                for idx, w in enumerate(wins):
                    w_type = type(w).__name__
                    has_nb = hasattr(w, "notebook")
                    nb_val = getattr(w, "notebook", None)
                    nb_type = type(nb_val).__name__ if nb_val else None
                    matching_attrs = [x for x in dir(w) if any(k in x.lower() for k in ["note", "page", "tab", "main"])]
                    self._write_ui_log(f"DEBUG win[{idx}]: type={w_type}, has_notebook={has_nb}, nb_type={nb_type}, matching_attrs={matching_attrs}")

            import gc
            main_windows = [obj for obj in gc.get_objects() if type(obj).__name__ == "MainWindow"]
            self._write_ui_log(f"DEBUG gc found MainWindows: {len(main_windows)}")
            for idx, mw in enumerate(main_windows):
                has_nb = hasattr(mw, "notebook")
                nb = getattr(mw, "notebook", None)
                self._write_ui_log(f"DEBUG MainWindow[{idx}]: id={hex(id(mw))}, has_notebook={has_nb}, nb={type(nb).__name__ if nb else None}")
                if has_nb and nb:
                    window = mw
                    self._write_ui_log(f"DEBUG using MainWindow from gc: {mw}")
                    break

            if not window or not getattr(window, "notebook", None):
                self._write_ui_log(f"window or notebook not ready yet (window={window}, notebook={getattr(window, 'notebook', None)}), retrying in 500ms...")
                GLib.timeout_add(500, self._setup_ui)
                return False

            if self.ui_page is not None:
                self._write_ui_log("ui_page is already initialized, skipping.")
                return False

            self._create_ui_widgets(window)
            self.refresh_ui()
            self._write_ui_log("SUCCESS: DELEECH UI attached to main window.")
        except Exception as e:
            self.ui_page = None
            self.treeview = None
            import traceback
            err = traceback.format_exc()
            self.log("Failed to initialize DELEECH UI tab: %s", err)
            self._write_ui_log(f"ERROR in _setup_ui:\n{err}")
        return False

    @staticmethod
    def _pack_widget(container, widget, expand=False, fill=False, padding=0):
        if hasattr(container, "append"):
            if expand:
                widget.set_hexpand(True)
                widget.set_vexpand(True)
            container.append(widget)
        else:
            container.pack_start(widget, expand, fill, padding)

    def _create_ui_widgets(self, window):
        from gi.repository import Gtk
        from pynicotine.gtkgui.widgets.treeview import TreeView

        self.ui_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.ui_page.id = "deleech"
        self.ui_page.content = self.ui_page
        self.ui_page.set_visible(True)

        page_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page_container.set_hexpand(True)
        page_container.set_vexpand(True)
        page_container.set_margin_top(6)
        page_container.set_margin_bottom(6)
        page_container.set_margin_start(6)
        page_container.set_margin_end(6)
        page_container.set_visible(True)
        self._pack_widget(self.ui_page, page_container, expand=True, fill=True)

        # Toolbar hierarchy required by MainWindow.show_header_bar / show_toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.set_visible(False)

        toolbar_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar_content.set_hexpand(True)
        toolbar_content.set_margin_top(6)
        toolbar_content.set_margin_bottom(6)
        toolbar_content.set_margin_start(6)
        toolbar_content.set_margin_end(6)
        toolbar_content.set_visible(True)
        self._pack_widget(toolbar, toolbar_content, expand=True, fill=True)

        toolbar_start_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar_start_content.set_halign(Gtk.Align.START)
        toolbar_start_content.set_hexpand(True)
        toolbar_start_content.set_valign(Gtk.Align.CENTER)
        toolbar_start_content.set_visible(True)
        self._pack_widget(toolbar_content, toolbar_start_content, expand=True, fill=True)

        self.filter_entry = Gtk.Entry()
        self.filter_entry.set_placeholder_text("Filter leechers...")
        self.filter_entry.set_width_chars(25)
        self.filter_entry.connect("changed", self._on_filter_changed)
        self._pack_widget(toolbar_start_content, self.filter_entry)

        toolbar_end_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar_end_content.set_valign(Gtk.Align.CENTER)
        toolbar_end_content.set_visible(True)
        self._pack_widget(toolbar_content, toolbar_end_content)

        btn_refresh = Gtk.Button(label="Refresh")
        btn_refresh.connect("clicked", self._on_refresh_clicked)
        self._pack_widget(toolbar_end_content, btn_refresh)

        btn_browse = Gtk.Button(label="Browse Shares")
        btn_browse.connect("clicked", self._on_browse_clicked)
        self._pack_widget(toolbar_end_content, btn_browse)

        btn_unban = Gtk.Button(label="Unban Selected")
        btn_unban.connect("clicked", self._on_unban_clicked)
        self._pack_widget(toolbar_end_content, btn_unban)

        btn_reset = Gtk.Button(label="Reset Strikes")
        btn_reset.connect("clicked", self._on_reset_clicked)
        self._pack_widget(toolbar_end_content, btn_reset)

        # Toolbar is the first child of page_container
        self._pack_widget(page_container, toolbar)

        # Status summary label
        self.stats_label = Gtk.Label(label="DELEECH: Initializing monitor...")
        self.stats_label.set_halign(Gtk.Align.START)
        self._pack_widget(page_container, self.stats_label)

        # ScrolledWindow & TreeView
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)

        try:
            from gi.repository import GObject
            has_gobject = True
        except ImportError:
            has_gobject = False

        columns = {
            "user": {
                "column_type": "text",
                "title": "Leecher",
                "width": 150,
                "default_sort_type": "ascending",
                "iterator_key": True,
            },
            "status": {
                "column_type": "text",
                "title": "Status",
                "width": 140,
            },
            "strikes": {
                "column_type": "number",
                "title": "Strikes",
                "width": 75,
                "sort_column": "strikes_data" if has_gobject else "strikes",
                "default_sort_type": "descending",
            },
            "strikes_total": {
                "column_type": "number",
                "title": "Total Strikes",
                "width": 95,
                "sort_column": "strikes_total_data" if has_gobject else "strikes_total",
            },
            "uploaded": {
                "column_type": "text",
                "title": "Uploaded / Quota",
                "width": 140,
                "sort_column": "uploaded_data" if has_gobject else "uploaded",
            },
            "ban_expiry": {
                "column_type": "text",
                "title": "Ban Expiry",
                "width": 160,
            },
            "unbans": {
                "column_type": "number",
                "title": "Unbans",
                "width": 75,
                "sort_column": "unbans_data" if has_gobject else "unbans",
            },
            "last_activity": {
                "column_type": "text",
                "title": "Last Activity",
                "width": 160,
            },
        }

        if has_gobject:
            columns["strikes_data"] = {"data_type": GObject.TYPE_UINT}
            columns["strikes_total_data"] = {"data_type": GObject.TYPE_UINT}
            columns["uploaded_data"] = {"data_type": GObject.TYPE_UINT64}
            columns["unbans_data"] = {"data_type": GObject.TYPE_UINT}

        # Seed and ensure config.sections["columns"]["deleech"] exists for persistence
        try:
            from pynicotine.config import config
            if hasattr(config, "sections") and "columns" in config.sections:
                col_conf = config.sections["columns"].setdefault("deleech", {})
                has_sort = any("sort" in props for props in col_conf.values() if isinstance(props, dict))
                if not has_sort:
                    saved_col = self.settings.get("sort_column", "strikes")
                    saved_order = self.settings.get("sort_order", "descending")
                    if saved_col:
                        col_conf.setdefault(saved_col, {})["sort"] = saved_order
        except Exception:
            pass

        self.treeview = TreeView(
            window=window,
            parent=scrolled,
            columns=columns,
            name="deleech",
            multi_select=True,
            persistent_sort=True,
            activate_row_callback=self._on_row_activated,
        )

        if hasattr(self.treeview, "model") and self.treeview.model:
            try:
                self.treeview.model.connect("sort-column-changed", self._on_sort_column_changed)
            except Exception:
                pass

        self._pack_widget(page_container, scrolled, expand=True, fill=True)

        class DeleechTabHandler:
            def __init__(self, page, toolbar, start_content, end_content, default_widget, treeview):
                self.page = page
                self.content = page
                self.toolbar = toolbar
                self.toolbar_start_content = start_content
                self.toolbar_end_content = end_content
                self.toolbar_default_widget = default_widget
                self.treeview = treeview

            def on_focus(self, *args):
                if self.treeview and hasattr(self.treeview, "widget"):
                    try:
                        self.treeview.widget.grab_focus()
                        return True
                    except Exception:
                        pass
                return False

            def destroy(self):
                pass

        self._tab_handler = DeleechTabHandler(
            page=self.ui_page,
            toolbar=toolbar,
            start_content=toolbar_start_content,
            end_content=toolbar_end_content,
            default_widget=self.filter_entry,
            treeview=self.treeview
        )

        if hasattr(window, "tabs") and isinstance(window.tabs, dict):
            window.tabs["deleech"] = self._tab_handler

        # Attach tab to main notebook
        window.notebook.append_page(self.ui_page, "DELEECH", focus_callback=self._tab_handler.on_focus)
        tab_label = window.notebook.get_tab_label(self.ui_page)
        if tab_label:
            tab_label.set_start_icon_name("security-medium-symbolic")
            if hasattr(tab_label, "container"):
                tab_label.container.set_visible(True)
        window.notebook.set_tab_reorderable(self.ui_page, True)
        if hasattr(window, "set_tab_expand"):
            window.set_tab_expand(self.ui_page)
        elif hasattr(window.notebook, "set_tab_expand"):
            window.notebook.set_tab_expand(self.ui_page, True)
        self.ui_page.set_visible(True)

        # Ensure modes_visible does not hide it
        try:
            from pynicotine.config import config
            if hasattr(config, "sections") and "ui" in config.sections:
                modes_visible = config.sections["ui"].get("modes_visible")
                if isinstance(modes_visible, dict):
                    modes_visible["deleech"] = True
        except Exception:
            pass

        self.log("DELEECH UI tab attached to main window.")

    def _teardown_ui(self):
        try:
            if self.ui_page is not None:
                window = None
                from pynicotine.gtkgui.application import Application
                if Application and hasattr(Application, "_instance") and Application._instance:
                    window = getattr(Application._instance, "window", None)
                if not window:
                    import gc
                    for mw in gc.get_objects():
                        if type(mw).__name__ == "MainWindow" and getattr(mw, "notebook", None):
                            window = mw
                            break

                if window:
                    if getattr(window, "current_page_id", None) == "deleech":
                        if hasattr(window, "notebook"):
                            for i in range(window.notebook.get_n_pages()):
                                p = window.notebook.get_nth_page(i)
                                if getattr(p, "id", None) != "deleech":
                                    window.notebook.set_current_page(p)
                                    break

                    if self._tab_handler:
                        start = getattr(self._tab_handler, "toolbar_start_content", None)
                        end = getattr(self._tab_handler, "toolbar_end_content", None)
                        if start and hasattr(window, "header_title"):
                            try:
                                if start.get_parent() == window.header_title:
                                    window.header_title.remove(start)
                            except Exception:
                                pass
                        if end and hasattr(window, "header_end_container"):
                            try:
                                if end.get_parent() == window.header_end_container:
                                    window.header_end_container.remove(end)
                            except Exception:
                                pass

                    if getattr(window, "notebook", None):
                        try:
                            window.notebook.remove_page(self.ui_page, None)
                        except TypeError:
                            window.notebook.remove_page(self.ui_page)

                    if hasattr(window, "tabs") and isinstance(window.tabs, dict):
                        window.tabs.pop("deleech", None)

                if self.treeview and hasattr(self.treeview, "save_columns"):
                    try:
                        self.treeview.save_columns()
                    except Exception:
                        pass

                self.ui_page = None
                self._tab_handler = None
                self.treeview = None
                self.stats_label = None
                self.filter_entry = None
                self._write_ui_log("DELEECH UI tab removed.")
        except Exception as e:
            self._write_ui_log(f"Failed to teardown DELEECH UI: {e}")
            self.log_debug("Failed to teardown DELEECH UI: %s", e)

    def _on_sort_column_changed(self, sortable):
        try:
            from gi.repository import Gtk
            sort_col_id, sort_type = sortable.get_sort_column_id()
            if self.treeview and hasattr(self.treeview, "_column_ids"):
                for name, idx in self.treeview._column_ids.items():
                    if idx == sort_col_id:
                        visible_name = name[:-5] if name.endswith("_data") else name
                        order_str = "descending" if sort_type == Gtk.SortType.DESCENDING else "ascending"
                        self.settings["sort_column"] = visible_name
                        self.settings["sort_order"] = order_str
                        break
        except Exception as e:
            self.log_debug("Failed to record sort column change: %s", e)


    def trigger_ui_refresh(self):
        try:
            from gi.repository import GLib
            GLib.idle_add(self.refresh_ui)
        except Exception:
            pass

    def refresh_ui(self):
        if not self.treeview or not self.ui_page:
            return

        try:
            self.csr.execute(
                "SELECT leecher, strikes, strikedate, is_banned, strikes_total, "
                "laststrikedate, unban_count, ban_end_date, mb_uploaded, last_state "
                "FROM strikes ORDER BY is_banned DESC, strikes DESC, mb_uploaded DESC"
            )
            db_rows = self.csr.fetchall()
            quota_mb = self.settings.get("leecher_quota_mb", 200)

            records = {}
            total_banned = 0
            total_mb = 0.0

            for row in db_rows:
                user, strikes, sdate, is_banned, total_s, last_sdate, unbans, ban_end, mb_up, last_st = row
                mb_val = float(mb_up or 0.0)
                total_mb += mb_val
                if is_banned:
                    total_banned += 1

                if is_banned:
                    status_str = "BANNED"
                elif user in self.probed_users:
                    st = self.probed_users[user]
                    if st == "pending_ban":
                        status_str = "Pending Ban"
                    elif st == "check_before_ban":
                        status_str = "Final Audit Before Ban"
                    elif st == "leecher_exceeded_quota":
                        status_str = "Quota Exceeded"
                    elif st.startswith("processed_leecher"):
                        status_str = f"Warned ({strikes} strikes)"
                    elif st == "pending_leecher":
                        status_str = "Warning Pending"
                    elif st == "requesting_shares":
                        status_str = "Auditing Shares"
                    elif st == "okay":
                        status_str = "Okay"
                    else:
                        status_str = st
                else:
                    status_str = last_st or (f"Warned ({strikes} strikes)" if strikes > 0 else "Inactive")

                records[user] = {
                    "user": user,
                    "status": status_str,
                    "strikes": strikes or 0,
                    "strikes_total": total_s or 0,
                    "uploaded": f"{mb_val:1.1f} / {quota_mb} MB",
                    "uploaded_raw_kb": int(mb_val * 1024),
                    "ban_expiry": ban_end if is_banned and ban_end else "-",
                    "unbans": unbans or 0,
                    "last_activity": last_sdate or sdate or "-",
                }

            # Also display users currently being probed in this session
            for user, st in self.probed_users.items():
                if user not in records and st != "okay":
                    if st == "requesting_stats":
                        st_str = "Requesting Stats"
                    elif st == "requesting_shares":
                        st_str = "Auditing Shares"
                    elif st == "pending_leecher":
                        st_str = "Warning Pending"
                    else:
                        st_str = st
                    records[user] = {
                        "user": user,
                        "status": st_str,
                        "strikes": 0,
                        "strikes_total": 0,
                        "uploaded": f"0.0 / {quota_mb} MB",
                        "uploaded_raw_kb": 0,
                        "ban_expiry": "-",
                        "unbans": 0,
                        "last_activity": "Active session",
                    }

            if self.stats_label:
                filter_note = f" (filtered by '{self._filter_text}')" if self._filter_text else ""
                self.stats_label.set_text(
                    f"Tracked Leechers: {len(records)} | Currently Banned: {total_banned} | "
                    f"Cumulative Upload: {total_mb:1.1f} MB{filter_note}"
                )

            # 1. Capture currently selected user(s)
            selected_users = []
            for iterator in self.treeview.get_selected_rows():
                u = self.treeview.get_row_value(iterator, "user")
                if u:
                    selected_users.append(u)

            filter_query = (self._filter_text or "").lower()
            visible_users = set()
            column_ids = [
                "user", "status", "strikes", "strikes_total",
                "uploaded", "ban_expiry", "unbans", "last_activity"
            ]
            has_hidden = hasattr(self.treeview, "_column_ids") and "strikes_data" in self.treeview._column_ids
            if has_hidden:
                column_ids.extend(["strikes_data", "strikes_total_data", "uploaded_data", "unbans_data"])

            for user, data in records.items():
                if filter_query and filter_query not in user.lower() and filter_query not in data["status"].lower():
                    continue

                visible_users.add(user)
                row_values = [
                    str(data["user"]),
                    str(data["status"]),
                    str(data["strikes"]),
                    str(data["strikes_total"]),
                    str(data["uploaded"]),
                    str(data["ban_expiry"]),
                    str(data["unbans"]),
                    str(data["last_activity"]),
                ]
                if has_hidden:
                    row_values.extend([
                        int(data.get("strikes", 0)),
                        int(data.get("strikes_total", 0)),
                        int(data.get("uploaded_raw_kb", 0)),
                        int(data.get("unbans", 0)),
                    ])

                if user in self.treeview.iterators:
                    iterator = self.treeview.iterators[user]
                    self.treeview.set_row_values(iterator, column_ids, row_values)
                else:
                    self.treeview.add_row(row_values, select_row=False)

            # 2. Remove rows that are no longer visible (e.g. filtered out or pruned)
            for user in list(self.treeview.iterators.keys()):
                if user not in visible_users:
                    iterator = self.treeview.iterators[user]
                    self.treeview.remove_row(iterator)

            # 3. Restore selection without scrolling or jumping viewport
            if selected_users:
                for u in selected_users:
                    if u in self.treeview.iterators:
                        self.treeview.select_row(self.treeview.iterators[u], should_scroll=False)
                        break

        except Exception as e:
            self.log_debug("Error updating DELEECH UI: %s", e)

    def _on_filter_changed(self, entry):
        self._filter_text = entry.get_text().strip()
        self.refresh_ui()

    def _on_refresh_clicked(self, button):
        self.refresh_ui()

    def _on_unban_clicked(self, button):
        if not self.treeview:
            return
        selected = list(self.treeview.get_selected_rows())
        if not selected:
            return
        for iterator in selected:
            user = self.treeview.get_row_value(iterator, "user")
            if user:
                self.unstrike_leecher(user)
        self.refresh_ui()

    def _on_reset_clicked(self, button):
        if not self.treeview:
            return
        selected = list(self.treeview.get_selected_rows())
        if not selected:
            return
        for iterator in selected:
            user = self.treeview.get_row_value(iterator, "user")
            if user:
                self.csr.execute(
                    "UPDATE strikes SET strikes=0, strikedate=NULL, mb_uploaded=0, last_state=NULL WHERE leecher=?",
                    [user]
                )
                self.conn.commit()
                if user in self.probed_users:
                    self.probed_users[user] = "okay"
                self.log("%s: strikes reset via UI", user)
        self.refresh_ui()

    def _on_browse_clicked(self, button):
        if not self.treeview:
            return
        selected = list(self.treeview.get_selected_rows())
        if not selected:
            return
        user = self.treeview.get_row_value(selected[0], "user")
        if user and hasattr(self.core, "userbrowse"):
            self.core.userbrowse.browse_user(user)

    def _on_row_activated(self, treeview, iterator, column_id):
        if not self.treeview:
            return
        user = self.treeview.get_row_value(iterator, "user")
        if user and hasattr(self.core, "userbrowse"):
            self.core.userbrowse.browse_user(user)
