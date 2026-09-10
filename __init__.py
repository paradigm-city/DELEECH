# SPDX-FileCopyrightText: 2020-2025 Nicotine+ Contributors
# SPDX-FileCopyrightText: 2011 quinox <quinox@users.sf.net>
# SPDX-License-Identifier: GPL-3.0-or-later

# Filename: __init__.py
# Author: paradigm_city
# Description: DELEECH plugin for nicotine+
# Version: 0.5
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

        config_folder_path, data_folder_path = config.get_user_folders()

        # database
        database_path = os.path.join(data_folder_path, "deleech.db")
        self.log("database: %s", database_path)
        self.conn = sqlite3.connect(database_path)
        self.csr = self.conn.cursor()

    def __del__(self):
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

    def is_suspect_user(self, user, num_files, num_folders, source="server"):
        folders = max(num_folders, 1)
        ratio = num_files / folders
        if num_files == 1000 and num_folders == 50:
            return True
        if ratio <= 1.3:
            return True
        if ratio > 2000:
            return True
        if (num_files % folders == 0) and (num_files % 50 == 0):
            return True
        return False

    def bans_2_days(self, bans):
        days = 10 ** (float(bans) / 5)
        return int(days)

    def check_user(self, user, num_files, num_folders, source="server"):
        if user not in self.probed_users:
            # We are not watching this user
            return

        if self.probed_users[user] == "okay":
            # User was already accepted previously, nothing to do
            return

        if (self.probed_users[user] == "requesting_shares" or self.probed_users[user] == "check_before_ban") and source != "peer":
            # Waiting for stats from peer, but received stats from server. Ignore.
            return

        self.log_debug("Checking user: %s", user)

        is_user_accepted = (num_files >= self.settings["num_files"] and num_folders >= self.settings["num_folders"])

        if self.is_suspect_user(user, num_files, num_folders, source):
            is_user_accepted = False
            force_user_check = True
            self.log_debug("%s: suspect, sharing %s files in %s folders", (user, num_files, num_folders))
        else:
            force_user_check = False

        if is_user_accepted or user in self.core.buddies.users:
            self.probed_users[user] = "okay"
            self.unstrike_leecher(user)

            if is_user_accepted:
                self.log_debug("%s: okay, sharing %s files in %s folders.", (user, num_files, num_folders))
            else:
                self.log_debug("%s: buddy is sharing %s files in %s folders. Not complaining.",
                               (user, num_files, num_folders))
            return
        else:
            # user was not accepted or buddy - check if a ban is pending
            self.log_debug("%s: NOT okay, sharing %s files in %s folders.", (user, num_files, num_folders))
            if self.probed_users[user] == "check_before_ban":
                self.log_debug("%s: arming a pending ban", user)
                self.probed_users[user] = "pending_ban"
                return

        if not (self.probed_users[user].startswith("requesting") or self.probed_users[user] == "check_before_ban"):
            # We already dealt with the user this session
            return

        self.csr.execute("SELECT count(*) FROM strikes where leecher=?", [user])
        rows = self.csr.fetchall()
        if rows[0][0] > 0:
            # We already messaged the user in a previous session
            self.probed_users[user] = "processed_leecher01"
            return

        if (num_files <= 0 or num_folders <= 0 or force_user_check) and self.probed_users[user] != "requesting_shares":
            # SoulseekQt only sends the number of shared files/folders to the server once on startup.
            # Verify user's actual number of files/folders.
            self.log_debug("%s: no shared files according to the server, requesting shares to verify…", user)

            self.probed_users[user] = "requesting_shares"
            self.log_debug("%s: request shares", user)
            self.core.userbrowse.request_user_shares(user)
            return

        log_message = ("%s: leecher detected, sharing %s files in %s folders. Going to %s leecher when transfer starts.")

        if self.settings["message"]:
            notification_type = "message"
        else:
            notification_type = "log"

        self.probed_users[user] = "pending_leecher"
        self.log_debug(log_message, (user, num_files, num_folders, notification_type))

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

        # We've received the user's stats in the past. They could be outdated by
        # now, so request them again.
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

    def user_stats_notification(self, user, stats):
        self.check_user(user, num_files=stats["files"], num_folders=stats["dirs"], source=stats["source"])

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
