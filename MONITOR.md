# DELEECH Monitor Window & Nicotine+ GUI Integration

## 1. Overview

The **DELEECH Monitor** is an integrated graphical user interface tab embedded directly into the primary **Nicotine+** desktop application window. It provides real-time visibility into:

- Active and historical leecher surveillance.
- Strike accumulations, quota consumption, and ban timers.
- Live share auditing and state machine progression.
- Direct interactive controls (unbanning, resetting strikes, auditing shares).

By providing transparent inspection and real-time feedback, the monitor eliminates the "black box" behavior of automated anti-leeching.

```
+----------------------------------------------------------------------------------------------------+
|  [Filter: text...   ]                   [Refresh] [Browse Shares] [Unban Selected] [Reset Strikes]  |
|  Tracked Leechers: 42 | Currently Banned: 3 | Cumulative Upload: 1,248.5 MB                        |
+----------------------------------------------------------------------------------------------------+
| Leecher    | Status            | Strikes | Total | Uploaded / Quota | Ban Expiry         | Unbans  |
+------------+-------------------+---------+-------+------------------+--------------------+---------+
| BadUser01  | BANNED            |    3    |   5   | 204.2 / 200 MB   | 2026-09-18 14:22   |    1    |
| SneakyPeer | Auditing Shares   |    1    |   1   |  45.0 / 200 MB   | -                  |    0    |
| HeavyLeech | Warned (2 strikes)|    2    |   2   | 188.0 / 200 MB   | -                  |    0    |
+----------------------------------------------------------------------------------------------------+
```

---

## 2. Nicotine+ Architecture & GUI Integration

Nicotine+ uses **GTK 4** with **libadwaita** (`Adw.ApplicationWindow`, `Adw.ViewStack` / `Gtk.Notebook`). Plugin UIs do not spawn disconnected dialogs; instead, they integrate as first-class tabs alongside native views (*Search*, *Downloads*, *Uploads*, *User Browse*, *Chat Rooms*).

### 2.1 System Architecture

```mermaid
flowchart TD
    subgraph NicotineCore["Nicotine+ Core Engine"]
        CoreFilter["Network Filter (Bans/Unbans)"]
        CoreUploads["Upload Manager"]
        CoreBrowse["User Browse Engine"]
        CoreUsers["User Stats Manager"]
    end

    subgraph DeleechPlugin["DELEECH Plugin Engine"]
        StateEngine["Surveillance State Machine (probed_users)"]
        SQLiteDB[("SQLite Database (deleech.db)")]
        PluginEvents["Plugin Event Hooks (upload_started, etc.)"]
    end

    subgraph NicotineGUI["Nicotine+ GTK4 / Adwaita GUI"]
        App["Gtk.Application / Gio.Application"]
        MainWin["MainWindow"]
        MainNotebook["Main Notebook / ViewStack"]
        HeaderBar["Window HeaderBar / Titlebar"]
        ConfigSys["Config System (sections['columns'])"]
    end

    subgraph DeleechUI["DELEECH Monitor Tab"]
        TabHandler["DeleechTabHandler Protocol Adapter"]
        UIPage["Root Gtk.Box ('deleech')"]
        Toolbar["Dynamic Toolbar & Action Buttons"]
        FilterEntry["Search / Filter Entry"]
        StatsLabel["Metrics Summary Label"]
        DeleechTree["Nicotine+ TreeView (Gtk.ScrolledWindow)"]
    end

    PluginEvents -->|Triggers| StateEngine
    PluginEvents -->|Reads/Writes| SQLiteDB
    StateEngine -->|GLib.idle_add| DeleechUI

    App -->|Hosts| MainWin
    MainWin -->|Contains| MainNotebook
    MainWin -->|Controls| HeaderBar

    DeleechUI -->|append_page| MainNotebook
    TabHandler -->|Registers in window.tabs| MainWin
    Toolbar -->|Yields widgets on switch| HeaderBar
    DeleechTree -->|Persists column layout| ConfigSys
```

---

## 3. UI Lifecycle & Asynchronous Window Discovery

Nicotine+ loads plugins during early engine initialization, typically before the GTK GUI event loop has created or exposed the `MainWindow`. Spawning GUI widgets synchronously during plugin load would fail or crash.

DELEECH utilizes non-blocking, asynchronous discovery via `GLib.idle_add`:

```mermaid
sequenceDiagram
    autonumber
    participant Core as Nicotine+ Core
    participant Plugin as DELEECH Plugin
    participant GLib as GLib MainLoop
    participant Win as GTK MainWindow

    Core->>Plugin: init() / loaded_notification()
    Plugin->>Plugin: dbinit(), _compile_banned_patterns()
    Note over Plugin,GLib: Asynchronous UI Attachment
    Plugin->>GLib: GLib.idle_add(self._setup_ui)
    
    loop Discovery Polling (500ms intervals if window not ready)
        GLib->>Plugin: _setup_ui()
        alt Window / Notebook available
            Plugin->>Win: Discover via Gio.Application / MainWindow
            Plugin->>Plugin: Cache self.window
            Plugin->>Win: _create_ui_widgets(window)
            Plugin->>Win: window.notebook.append_page(ui_page, "DELEECH")
            Plugin->>Win: window.tabs["deleech"] = tab_handler
            Plugin->>Plugin: refresh_ui()
        else Window not ready yet
            Plugin->>GLib: GLib.timeout_add(500, self._setup_ui)
        end
    end

    Note over Plugin,Win: Normal Execution & Event Refresh
    Core->>Plugin: upload_started_notification()
    Plugin->>Plugin: Update state / SQLite
    Plugin->>GLib: GLib.idle_add(self.refresh_ui)
    GLib->>Plugin: refresh_ui()
    Plugin->>Win: In-place TreeView delta update

    Note over Core,Plugin: Clean Teardown on Disable / Reload
    Core->>Plugin: disable() / unloaded_notification()
    Plugin->>Win: Save column widths & sort order
    Plugin->>Win: Switch active page if current == "deleech"
    Plugin->>Win: window.notebook.remove_page(ui_page)
    Plugin->>Win: Delete window.tabs["deleech"]
    Plugin->>Plugin: Clear references (self.window = None, etc.)
```

### 3.1 Window Discovery Hierarchy

To locate the active application window safely across different Nicotine+ versions and GTK packaging environments without locking the UI thread, `_setup_ui()` follows a prioritized hierarchy:

1. **Cached Reference**: Checks `self.window` (fast-path).
2. **Nicotine Application Instance**: Queries `pynicotine.gtkgui.application.Application._instance.window`.
3. **GLib / Gio Application Registry**: Queries `Gio.Application.get_default().get_windows()` for open windows exposing `.notebook`.
4. **Garbage Collection Object Inspection**: As a defensive fallback, inspects `gc.get_objects()` for active `MainWindow` instances.
5. **Debounced Retry**: If the GUI tree is still being assembled, queues a retry in `500ms` via `GLib.timeout_add(500, self._setup_ui)`.

---

## 4. Component Hierarchy & Layout

The UI implements a vertical box layout with integrated spacing and margins matching the standard GNOME Human Interface Guidelines (HIG):

```mermaid
graph TD
    subgraph WindowFrame["Nicotine+ MainWindow Notebook"]
        UIPage["self.ui_page: Gtk.Box (Orientation: Vertical, ID: 'deleech')"]
    end

    subgraph PageContainer["page_container: Gtk.Box (Margins: 6px, Spacing: 6px)"]
        ToolbarBox["toolbar: Gtk.Box (Dynamic Toolbar)"]
        StatsBox["self.stats_label: Gtk.Label (Summary Metrics)"]
        ScrollBox["scrolled: Gtk.ScrolledWindow (VExpand: True, HExpand: True)"]
    end

    subgraph ToolbarHierarchy["Toolbar Content"]
        StartContent["toolbar_start_content: Gtk.Box (Align: Start)"]
        EndContent["toolbar_end_content: Gtk.Box (Align: End)"]
        Filter["self.filter_entry: Gtk.Entry ('Filter leechers...')"]
        BtnRefresh["btn_refresh: Gtk.Button ('Refresh')"]
        BtnBrowse["btn_browse: Gtk.Button ('Browse Shares')"]
        BtnUnban["btn_unban: Gtk.Button ('Unban Selected')"]
        BtnReset["btn_reset: Gtk.Button ('Reset Strikes')"]
    end

    subgraph TreeViewContainer["Nicotine+ TreeView Component"]
        TreeWidget["self.treeview: TreeView (multi_select=True, persistent_sort=True)"]
        ListStore["Gtk.ListStore (8 Visible + 4 Hidden GObject Data Columns)"]
    end

    UIPage --> PageContainer
    PageContainer --> ToolbarBox
    PageContainer --> StatsBox
    PageContainer --> ScrollBox

    ToolbarBox --> StartContent
    ToolbarBox --> EndContent
    StartContent --> Filter
    EndContent --> BtnRefresh
    EndContent --> BtnBrowse
    EndContent --> BtnUnban
    EndContent --> BtnReset

    ScrollBox --> TreeWidget
    TreeWidget --> ListStore
```

### 4.1 Tab Protocol Adapter (`DeleechTabHandler`)

Nicotine+'s main window switches toolbars dynamically when the user tabs between views. For a plugin tab to receive focus and dock toolbar items into the main window header, it must adhere to the `MainWindow.tabs` protocol:

- **`page` & `content`**: The root `Gtk.Box` page widget.
- **`toolbar`**: The secondary container for standard toolbar mode.
- **`toolbar_start_content`**: Contains the live filter input (`Gtk.Entry`). When header bar mode is active, `MainWindow` elevates this widget into the primary titlebar.
- **`toolbar_end_content`**: Contains the action buttons (*Refresh*, *Browse Shares*, *Unban Selected*, *Reset Strikes*). In header bar mode, this docks into `window.header_end_container`.
- **`toolbar_default_widget`**: Directs default keyboard focus to the search filter entry.
- **`on_focus()`**: Automatically redirects focus to the TreeView for immediate arrow-key navigation.

---

## 5. TreeView & Numeric Sorting Architecture

A common defect in desktop table widgets is **lexicographical string sorting** of numerical and byte data (e.g., `"100 MB"` appearing before `"20 MB"`, or `"9"` appearing after `"10"`).

DELEECH resolves this by using **dual-column mapping** backed by native GObject primitives:

```mermaid
flowchart LR
    subgraph DataPipeline["Data Formatting in refresh_ui()"]
        RawDB[("Raw SQLite Values")]
        RawMB["mb_uploaded: 128.5 MB"]
        RawStrikes["strikes: 3"]
        RawUnbans["unban_count: 1"]
    end

    subgraph ListStoreRecord["Gtk.ListStore Row"]
        subgraph DisplayCols["Human-Visible Text Columns"]
            ColUser["'BadUser'"]
            ColStatus["'Warned (3 strikes)'"]
            ColStrikesText["'3'"]
            ColTotalText["'7'"]
            ColUpText["'128.5 / 200 MB'"]
            ColExpiryText["'-'"]
            ColUnbanText["'1'"]
            ColDateText["'2026-09-14 21:30'"]
        end

        subgraph HiddenCols["Hidden GObject Typed Sort Columns"]
            ColStrikesData["strikes_data: TYPE_UINT (3)"]
            ColTotalData["strikes_total_data: TYPE_UINT (7)"]
            ColUpData["uploaded_data: TYPE_UINT64 (131584 KB)"]
            ColUnbansData["unbans_data: TYPE_UINT (1)"]
        end
    end

    subgraph ColumnSortMapping["TreeView Column Definition"]
        ViewStrikes["Column 'Strikes' -> sort_column: 'strikes_data'"]
        ViewTotal["Column 'Total Strikes' -> sort_column: 'strikes_total_data'"]
        ViewUp["Column 'Uploaded / Quota' -> sort_column: 'uploaded_data'"]
        ViewUnbans["Column 'Unbans' -> sort_column: 'unbans_data'"]
    end

    RawDB --> DataPipeline
    RawMB --> ColUpText
    RawMB --> ColUpData
    RawStrikes --> ColStrikesText
    RawStrikes --> ColStrikesData
    RawUnbans --> ColUnbanText
    RawUnbans --> ColUnbansData

    DisplayCols -.-> ColumnSortMapping
    HiddenCols --> ColumnSortMapping
```

### 5.1 Column Specifications

| Column ID | Visible Header | Type | Width | Sort Column | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `user` | **Leecher** | Text | 150 px | Self (Ascending) | Peer username; acts as unique row iterator key. |
| `status` | **Status** | Text | 140 px | Self | Current enforcement status or state machine badge. |
| `strikes` | **Strikes** | Number | 75 px | `strikes_data` (UINT) | Current warning strikes count (default sort: Descending). |
| `strikes_total`| **Total Strikes** | Number | 95 px | `strikes_total_data` (UINT) | Cumulative lifetime strike count. |
| `uploaded` | **Uploaded / Quota**| Text | 140 px | `uploaded_data` (UINT64) | Bandwidth transferred vs configured leecher limit (in MB). |
| `ban_expiry` | **Ban Expiry** | Text | 160 px | Self | ISO timestamp when ban unlocks (or `-` if not banned). |
| `unbans` | **Unbans** | Number | 75 px | `unbans_data` (UINT) | Number of times the peer has been unbanned. |
| `last_activity`| **Last Activity**| Text | 160 px | Self | Timestamp of most recent strike, scan, or session event. |

### 5.2 Persistence of Sort & Column Preferences

1. **Native Nicotine+ Configuration**: Automatically registers into `config.sections["columns"]["deleech"]`. Column widths, positions, and visibility are persisted natively by Nicotine+.
2. **Plugin Fallback Settings**: When the user clicks a column header, `_on_sort_column_changed()` intercepts the signal and records `sort_column` and `sort_order` directly into `self.settings` to guarantee persistence across plugin reloads.

---

## 6. Real-Time Data Pipeline & Non-Disruptive Refresh

Anti-leech tracking is highly concurrent: upload notifications, stats arrivals, and peer share audits occur continuously in the background.

To keep the UI responsive and prevent list viewport scrolling or selection loss during updates, `refresh_ui()` employs an **in-place delta update algorithm**:

```mermaid
flowchart TD
    Trigger["Event: upload_started / stats / ban / button"] --> TriggerCall["self.trigger_ui_refresh()"]
    TriggerCall --> IdleSchedule["GLib.idle_add(self.refresh_ui)"]

    subgraph RefreshPipeline["refresh_ui() Execution"]
        ReadDB["1. Query SQLite: SELECT ... FROM strikes ORDER BY is_banned DESC, strikes DESC"]
        MergeMemory["2. Merge in-memory active sessions (self.probed_users)"]
        ComputeStats["3. Compute totals: Tracked, Banned, Cumulative MB"]
        UpdateStatsLabel["4. Update stats_label text with filter note"]

        CaptureSelection["5. Capture active selection: selected_users = [row.user ...]"]
        
        FilterLoop{"6. For each record in records"}
        MatchQuery{"Matches filter text?"}
        RowExists{"User key in treeview.iterators?"}

        InPlaceUpdate["treeview.set_row_values(iter, column_ids, values)"]
        InsertRow["treeview.add_row(values, select_row=False)"]
        SkipRow["Skip record"]

        PruneObsolete["7. Prune rows not in visible_users: treeview.remove_row(iter)"]
        RestoreSelection["8. Restore selection: select_row(iter, should_scroll=False)"]
    end

    IdleSchedule --> ReadDB
    ReadDB --> MergeMemory
    MergeMemory --> ComputeStats
    ComputeStats --> UpdateStatsLabel
    UpdateStatsLabel --> CaptureSelection
    CaptureSelection --> FilterLoop

    FilterLoop --> MatchQuery
    MatchQuery -- Yes --> RowExists
    MatchQuery -- No --> SkipRow

    RowExists -- Yes --> InPlaceUpdate
    RowExists -- No --> InsertRow

    InPlaceUpdate --> PruneObsolete
    InsertRow --> PruneObsolete
    SkipRow --> PruneObsolete

    PruneObsolete --> RestoreSelection
```

### 6.1 State String Mapping

The monitor translates raw internal workflow states into clear status badges:

```mermaid
stateDiagram-v2
    [*] --> ActiveSession: Peer Queues Upload

    state ActiveSession {
        [*] --> RequestingStats: First seen
        RequestingStats --> AuditingShares: Suspicious / Missing Stats
        AuditingShares --> WarningPending: Under Threshold
        AuditingShares --> Okay: Threshold Met
        WarningPending --> Warned: Upload Starts (Warning Sent)
        Warned --> QuotaExceeded: Transferred > leecher_quota_mb
        Warned --> CheckBeforeBan: Strikes >= auto_ban_after
        CheckBeforeBan --> PendingBan: Final Audit Failed
        CheckBeforeBan --> Okay: Corrected Shares
        PendingBan --> BANNED: Strike Executed
        QuotaExceeded --> BANNED: Strike Executed
    }

    state PersistentDB {
        BANNED --> Inactive: Ban Expires / Unbanned
        Warned --> Inactive: Strikes Stale (> 90 days)
        Inactive --> ActiveSession: Peer Queues Upload
    }
```

| State Machine State | Displayed Badge in Monitor | Condition |
| :--- | :--- | :--- |
| `is_banned == 1` | `BANNED` | User is currently banned in `core.network_filter`. |
| `pending_ban` | `Pending Ban` | Final warning issued; ban will trigger on next transfer start. |
| `check_before_ban` | `Final Audit Before Ban` | User is undergoing last-chance direct peer share audit before ban. |
| `leecher_exceeded_quota` | `Quota Exceeded` | User exceeded maximum allowed bandwidth transfer without sharing. |
| `processed_leecher<nn>` | `Warned (<n> strikes)` | User has received warnings and accumulated strikes. |
| `pending_leecher` | `Warning Pending` | Identified as non-sharing; warning message queued. |
| `requesting_shares` | `Auditing Shares` | Inspecting peer's shared directories directly. |
| `requesting_stats` | `Requesting Stats` | Fetching initial stats from Soulseek server. |
| `okay` | `Okay` | Peer satisfies sharing requirements. |
| DB record (no active state) | `Warned (<n> strikes)` or `Inactive` | Historical record stored in SQLite; not currently transferring. |

---

## 7. Interactive User Controls

The monitor toolbar and row actions give the operator full manual control over the automated enforcement engine:

```mermaid
flowchart TD
    subgraph UIControls["User Actions"]
        ActFilter["Typing in Filter Entry"]
        ActRefresh["Click 'Refresh' Button"]
        ActBrowse["Click 'Browse Shares' or Double-Click Row"]
        ActUnban["Click 'Unban Selected'"]
        ActReset["Click 'Reset Strikes'"]
    end

    subgraph Handlers["DELEECH Action Handlers"]
        HFilter["_on_filter_changed()"]
        HRefresh["_on_refresh_clicked()"]
        HBrowse["_on_browse_clicked() / _on_row_activated()"]
        HUnban["_on_unban_clicked()"]
        HReset["_on_reset_clicked()"]
    end

    subgraph Effects["System Effects"]
        EffFilter["Filters visible rows in real-time by username or status"]
        EffRefresh["Refetches SQLite database and active session states"]
        EffBrowse["core.userbrowse.browse_user(username) in Nicotine+"]
        EffUnban["unstrike_leecher(user) -> Unbans in core and clears ban in DB"]
        EffReset["Resets strikes=0, mb_uploaded=0, last_state=NULL in SQLite & memory"]
    end

    ActFilter --> HFilter --> EffFilter
    ActRefresh --> HRefresh --> EffRefresh
    ActBrowse --> HBrowse --> EffBrowse
    ActUnban --> HUnban --> EffUnban
    ActReset --> HReset --> EffReset
    
    EffUnban --> EffRefresh
    EffReset --> EffRefresh
```

### 7.1 Action Reference

- **Search Filter Entry**:
  - Live, debounced text search.
  - Matches case-insensitively against both **Username** and **Status** (e.g., typing `banned` displays all currently banned users; typing a username isolates that peer).
- **Refresh**:
  - Manually re-reads `deleech.db` and merges active session states.
- **Browse Shares**:
  - Direct integration with `core.userbrowse.browse_user(user)`.
  - Opens Nicotine+'s native share browser tab for the selected peer.
  - Also triggered by **double-clicking** any row or pressing **Enter**.
- **Unban Selected**:
  - Supports multi-selection.
  - Invokes `self.unstrike_leecher(user)`:
    - Removes peer from Nicotine+'s active ban filter (`core.network_filter.unban_user`).
    - Sets `is_banned = 0`, increments `unban_count`, timestamps `unban_date`, and clears `ban_end_date`.
- **Reset Strikes**:
  - Supports multi-selection.
  - Resets active strike count, uploaded MB, and status flags back to zero in the database while retaining lifetime audit history (`strikes_total`).
  - Sets active state to `"okay"`, giving the peer a clean slate.

---

## 8. Configuration & Toggle Options

The monitor tab can be toggled or configured in the plugin settings (`PLUGININFO` / `self.settings`):

- **`show_ui_tab`** (Boolean, default: `True`):
  - When `True`, the DELEECH tab is created and attached to the main window.
  - When `False`, DELEECH runs as a silent headless background daemon without allocating GTK widgets.
- **`sort_column`** (String, default: `"strikes"`):
  - Preserves the user's selected sort column.
- **`sort_order`** (String, default: `"descending"`):
  - Preserves sort orientation (`"ascending"` or `"descending"`).
- **`leecher_quota_mb`** (Integer, default: `200`):
  - Displayed in the `Uploaded / Quota` column as the maximum threshold before an automatic quota ban triggers.
