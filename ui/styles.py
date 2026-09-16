"""Gradio application styles."""

CSS = """
/* AI Cartoon Factory UI
   Cleaned presentation layer aligned with design-system/MASTER.md.
   Keep this file visual-only: no behavior, data flow, or feature changes. */

:root,
:root[data-aicf-theme="linear"],
body[data-aicf-theme="linear"] {
    --theme-bg: #f7f8fa;
    --theme-bg-subtle: #f4f6f8;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #f9fafb;
    --theme-surface-muted: #f1f5f9;
    --theme-border: #e5e7eb;
    --theme-border-strong: #cbd5e1;
    --theme-text-title: #0f172a;
    --theme-text-card: #1f2937;
    --theme-text-body: #374151;
    --theme-text-secondary: #4b5563;
    --theme-text-label: #374151;
    --theme-text-placeholder: #6b7280;
    --theme-text-disabled: #d1d5db;
    --theme-accent: #2563eb;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #1d4ed8;
    --theme-accent-soft: #eff6ff;
    --theme-success: #22c55e;
    --theme-success-text: #15803d;
    --theme-success-soft: #f0fdf4;
    --theme-warning: #f59e0b;
    --theme-warning-text: #b45309;
    --theme-warning-soft: #fffbeb;
    --theme-danger: #ef4444;
    --theme-danger-text: #b91c1c;
    --theme-danger-soft: #fef2f2;
    --theme-focus: rgba(37, 99, 235, 0.18);
    --theme-accent-border: #bfdbfe;
    --theme-accent-border-strong: #93c5fd;
    --theme-accent-soft-hover: #dbeafe;
    --theme-success-border: #bbf7d0;
    --theme-warning-border: #fde68a;
    --theme-danger-border: #fecaca;
    --theme-scrollbar-thumb: #cbd5e1;
    --theme-overlay-scrim: rgba(15, 23, 42, 0.36);
    --theme-preview-scrim: rgba(15, 23, 42, 0.5);
    --theme-active-ring: 0 0 0 3px rgba(37, 99, 235, 0.12);
    --theme-danger-ring: 0 0 0 3px rgba(239, 68, 68, 0.12);
    --theme-radius-sm: 8px;
    --theme-radius: 10px;
    --theme-radius-lg: 12px;
    --theme-shadow: 0 1px 2px rgba(15, 23, 42, 0.035);
    --theme-shadow-hover: 0 8px 22px rgba(15, 23, 42, 0.055);
    --theme-shadow-overlay: 0 18px 44px rgba(15, 23, 42, 0.14);
    --theme-motion: 170ms ease;
    --theme-font-ui: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    --theme-font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Cascadia Mono", "Microsoft YaHei UI", monospace;

    --bg: var(--theme-bg);
    --bg-subtle: var(--theme-bg-subtle);
    --surface: var(--theme-surface);
    --surface-subtle: var(--theme-surface-subtle);
    --surface-muted: var(--theme-surface-muted);
    --border: var(--theme-border);
    --border-strong: var(--theme-border-strong);
    --text-title: var(--theme-text-title);
    --text-card: var(--theme-text-card);
    --text-body: var(--theme-text-body);
    --text-secondary: var(--theme-text-secondary);
    --text-label: var(--theme-text-label);
    --text-placeholder: var(--theme-text-placeholder);
    --text-disabled: var(--theme-text-disabled);
    --accent: var(--theme-accent);
    --accent-foreground: var(--theme-accent-foreground);
    --accent-hover: var(--theme-accent-hover);
    --accent-soft: var(--theme-accent-soft);
    --success: var(--theme-success);
    --success-text: var(--theme-success-text);
    --success-soft: var(--theme-success-soft);
    --warning: var(--theme-warning);
    --warning-text: var(--theme-warning-text);
    --warning-soft: var(--theme-warning-soft);
    --danger: var(--theme-danger);
    --danger-text: var(--theme-danger-text);
    --danger-soft: var(--theme-danger-soft);
    --focus: var(--theme-focus);
    --accent-border: var(--theme-accent-border);
    --accent-border-strong: var(--theme-accent-border-strong);
    --accent-soft-hover: var(--theme-accent-soft-hover);
    --success-border: var(--theme-success-border);
    --warning-border: var(--theme-warning-border);
    --danger-border: var(--theme-danger-border);
    --scrollbar-thumb: var(--theme-scrollbar-thumb);
    --overlay-scrim: var(--theme-overlay-scrim);
    --preview-scrim: var(--theme-preview-scrim);
    --active-ring: var(--theme-active-ring);
    --danger-ring: var(--theme-danger-ring);
    --radius-sm: var(--theme-radius-sm);
    --radius: var(--theme-radius);
    --radius-lg: var(--theme-radius-lg);
    --shadow: var(--theme-shadow);
    --shadow-hover: var(--theme-shadow-hover);
    --shadow-overlay: var(--theme-shadow-overlay);
    --motion: var(--theme-motion);
    --font-ui: var(--theme-font-ui);
    --font-mono: var(--theme-font-mono);
}

:root[data-aicf-theme="cursor"],
body[data-aicf-theme="cursor"] {
    --theme-bg: #f7f7fb;
    --theme-bg-subtle: #f1f2f8;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #f7f6ff;
    --theme-surface-muted: #ede9fe;
    --theme-border: #dedaf0;
    --theme-border-strong: #bcb4d8;
    --theme-text-title: #18181b;
    --theme-text-card: #27272a;
    --theme-text-body: #3f3f46;
    --theme-text-secondary: #71717a;
    --theme-text-label: #52525b;
    --theme-text-placeholder: #8b8ba3;
    --theme-text-disabled: #c4c4cf;
    --theme-accent: #8b5cf6;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #7c3aed;
    --theme-accent-soft: #f3e8ff;
    --theme-success: #22c55e;
    --theme-success-text: #15803d;
    --theme-success-soft: #f0fdf4;
    --theme-warning-text: #b45309;
    --theme-warning-soft: #fffbeb;
    --theme-danger-text: #b91c1c;
    --theme-danger-soft: #fef2f2;
    --theme-focus: rgba(139, 92, 246, 0.18);
    --theme-accent-border: #ddd6fe;
    --theme-accent-border-strong: #c4b5fd;
    --theme-accent-soft-hover: #ede9fe;
    --theme-success-border: #bbf7d0;
    --theme-warning-border: #fde68a;
    --theme-danger-border: #fecaca;
    --theme-scrollbar-thumb: #c4b5fd;
    --theme-overlay-scrim: rgba(24, 24, 27, 0.42);
    --theme-preview-scrim: rgba(24, 24, 27, 0.58);
    --theme-active-ring: 0 0 0 3px rgba(139, 92, 246, 0.14);
    --theme-danger-ring: 0 0 0 3px rgba(239, 68, 68, 0.12);
    --theme-shadow: 0 1px 2px rgba(76, 29, 149, 0.045);
    --theme-shadow-hover: 0 8px 22px rgba(76, 29, 149, 0.07);
    --theme-shadow-overlay: 0 18px 44px rgba(76, 29, 149, 0.16);
}

:root[data-aicf-theme="vercel"],
body[data-aicf-theme="vercel"] {
    --theme-bg: #ffffff;
    --theme-bg-subtle: #fafafa;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #fafafa;
    --theme-surface-muted: #f4f4f5;
    --theme-border: #e4e4e7;
    --theme-border-strong: #a1a1aa;
    --theme-text-title: #000000;
    --theme-text-card: #18181b;
    --theme-text-body: #27272a;
    --theme-text-secondary: #71717a;
    --theme-text-label: #3f3f46;
    --theme-accent: #000000;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #27272a;
    --theme-accent-soft: #f4f4f5;
    --theme-focus: rgba(24, 24, 27, 0.18);
    --theme-accent-border: #d4d4d8;
    --theme-accent-border-strong: #a1a1aa;
    --theme-accent-soft-hover: #e4e4e7;
    --theme-radius-sm: 7px;
    --theme-radius: 8px;
    --theme-radius-lg: 10px;
}

:root[data-aicf-theme="glass"],
body[data-aicf-theme="glass"] {
    --theme-bg: #eef2ff;
    --theme-bg-subtle: #f8fafc;
    --theme-surface: rgba(255, 255, 255, 0.78);
    --theme-surface-subtle: rgba(255, 255, 255, 0.54);
    --theme-surface-muted: rgba(238, 242, 255, 0.72);
    --theme-border: rgba(148, 163, 184, 0.36);
    --theme-border-strong: rgba(99, 102, 241, 0.32);
    --theme-accent: #6366f1;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #4f46e5;
    --theme-accent-soft: rgba(99, 102, 241, 0.12);
    --theme-radius-sm: 10px;
    --theme-radius: 12px;
    --theme-radius-lg: 16px;
    --theme-shadow: 0 8px 24px rgba(79, 70, 229, 0.08);
    --theme-shadow-hover: 0 16px 36px rgba(79, 70, 229, 0.12);
    --theme-accent-border: rgba(129, 140, 248, 0.34);
    --theme-accent-border-strong: rgba(129, 140, 248, 0.48);
    --theme-accent-soft-hover: rgba(99, 102, 241, 0.18);
}

:root[data-aicf-theme="ocean"],
body[data-aicf-theme="ocean"] {
    --theme-bg: #f0f9ff;
    --theme-bg-subtle: #f8fafc;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #f0f9ff;
    --theme-border: #bae6fd;
    --theme-border-strong: #7dd3fc;
    --theme-accent: #0284c7;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #0369a1;
    --theme-accent-soft: #e0f2fe;
    --theme-focus: rgba(2, 132, 199, 0.18);
    --theme-accent-border: #bae6fd;
    --theme-accent-border-strong: #7dd3fc;
    --theme-accent-soft-hover: #bae6fd;
}

:root[data-aicf-theme="purple"],
body[data-aicf-theme="purple"] {
    --theme-bg: #faf5ff;
    --theme-bg-subtle: #fbf7ff;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #faf5ff;
    --theme-border: #e9d5ff;
    --theme-border-strong: #d8b4fe;
    --theme-accent: #7c3aed;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #6d28d9;
    --theme-accent-soft: #f3e8ff;
    --theme-focus: rgba(124, 58, 237, 0.18);
    --theme-accent-border: #ddd6fe;
    --theme-accent-border-strong: #c4b5fd;
    --theme-accent-soft-hover: #ede9fe;
}

:root[data-aicf-theme="cyber"],
body[data-aicf-theme="cyber"] {
    --theme-bg: #f5fbff;
    --theme-bg-subtle: #eef8ff;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #f0f9ff;
    --theme-surface-muted: #e0f2fe;
    --theme-border: #bae6fd;
    --theme-border-strong: #7dd3fc;
    --theme-text-title: #082f49;
    --theme-text-card: #0f172a;
    --theme-text-body: #334155;
    --theme-text-secondary: #64748b;
    --theme-text-label: #0369a1;
    --theme-text-placeholder: #94a3b8;
    --theme-text-disabled: #cbd5e1;
    --theme-accent: #22d3ee;
    --theme-accent-foreground: #083344;
    --theme-accent-hover: #0891b2;
    --theme-accent-soft: #cffafe;
    --theme-success-text: #15803d;
    --theme-success-soft: #f0fdf4;
    --theme-warning-text: #b45309;
    --theme-warning-soft: #fffbeb;
    --theme-danger-text: #b91c1c;
    --theme-danger-soft: #fef2f2;
    --theme-focus: rgba(34, 211, 238, 0.18);
    --theme-accent-border: #a5f3fc;
    --theme-accent-border-strong: #67e8f9;
    --theme-accent-soft-hover: #a5f3fc;
    --theme-success-border: #bbf7d0;
    --theme-warning-border: #fde68a;
    --theme-danger-border: #fecaca;
    --theme-scrollbar-thumb: #7dd3fc;
    --theme-overlay-scrim: rgba(8, 47, 73, 0.42);
    --theme-preview-scrim: rgba(8, 47, 73, 0.58);
    --theme-active-ring: 0 0 0 3px rgba(34, 211, 238, 0.16);
    --theme-danger-ring: 0 0 0 3px rgba(239, 68, 68, 0.12);
    --theme-shadow: 0 1px 2px rgba(14, 116, 144, 0.045);
    --theme-shadow-hover: 0 8px 22px rgba(14, 116, 144, 0.07);
    --theme-shadow-overlay: 0 18px 44px rgba(14, 116, 144, 0.16);
}

:root[data-aicf-theme="classic"],
body[data-aicf-theme="classic"] {
    --theme-bg: #ffffff;
    --theme-bg-subtle: #f8fafc;
    --theme-surface: #ffffff;
    --theme-surface-subtle: #f8fafc;
    --theme-surface-muted: #f1f5f9;
    --theme-border: #dbe3ee;
    --theme-border-strong: #b6c2d1;
    --theme-accent: #2563eb;
    --theme-accent-foreground: #ffffff;
    --theme-accent-hover: #1d4ed8;
    --theme-accent-soft: #eff6ff;
    --theme-accent-border: #bfdbfe;
    --theme-accent-border-strong: #93c5fd;
    --theme-accent-soft-hover: #dbeafe;
    --theme-radius-sm: 6px;
    --theme-radius: 8px;
    --theme-radius-lg: 10px;
}

* {
    box-sizing: border-box;
}

body,
.gradio-container {
    min-height: 100vh;
    background: var(--bg) !important;
    color: var(--text-body) !important;
    font-family: var(--font-ui) !important;
    font-size: 14px !important;
    line-height: 1.5 !important;
    letter-spacing: 0 !important;
}

.gradio-container {
    width: min(100%, 2360px) !important;
    max-width: calc(100vw - 48px) !important;
    margin: 0 auto !important;
    padding: 18px 24px 26px !important;
}

.prose,
.prose p,
.prose li {
    color: var(--text-body) !important;
}

.muted {
    color: var(--text-secondary);
    font-size: 13px;
}

/* Header */
.app-header {
    display: grid !important;
    grid-template-columns: 250px minmax(520px, 1fr) minmax(250px, 0.72fr) !important;
    align-items: center !important;
    gap: 12px !important;
    min-height: 74px !important;
    margin: 0 !important;
    padding: 10px 14px !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent-soft) 24%, var(--surface)) 0%, var(--surface) 100%) !important;
    box-shadow: var(--shadow) !important;
}

.app-header > *:first-child,
.app-header > *:nth-child(2),
.app-header > *:last-child {
    min-width: 0 !important;
}

.brand-lockup {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    height: 48px;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: color-mix(in srgb, var(--surface) 86%, transparent);
}

.brand-mark {
    width: 30px;
    height: 30px;
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--accent-border);
    border-radius: var(--radius-sm);
    background: var(--accent-soft);
    color: var(--accent);
    font-size: 11px;
    font-weight: 750;
    line-height: 1;
}

.brand-title {
    color: var(--text-title);
    font-size: 14px;
    font-weight: 720;
    line-height: 1.2;
}

.brand-subtitle {
    margin-top: 2px;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.35;
}

.top-actions {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 8px;
    min-width: 250px;
}

.app-header .gear-icon,
.app-header .open-videos-dir-btn,
.app-header .preview-final-btn {
    width: 38px !important;
    min-width: 38px !important;
    height: 38px !important;
    min-height: 38px !important;
    padding: 0 !important;
    font-size: 15px !important;
    border-radius: 8px !important;
}

.app-header .open-videos-dir-btn svg {
    width: 18px;
    height: 18px;
    fill: none;
    stroke: var(--accent);
    stroke-width: 2.2;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.app-header .open-videos-dir-btn {
    border-color: var(--accent-border-strong) !important;
    background: var(--accent-soft) !important;
    color: var(--accent) !important;
}

.app-header .open-videos-dir-btn:hover {
    border-color: var(--accent) !important;
    background: var(--accent-soft-hover) !important;
    color: var(--accent-hover) !important;
}

.app-header .open-videos-dir-btn:hover svg {
    stroke: var(--accent-hover);
}

#main_page_tabs {
    max-width: 560px;
    margin: 10px 0 2px;
}

#main_page_tabs .wrap,
#main_page_tabs .form {
    border: 0 !important;
    box-shadow: none !important;
    padding: 0 !important;
    background: transparent !important;
}

#main_page_tabs > label,
#main_page_tabs .label-wrap,
#main_page_tabs .block-info,
#main_page_tabs .sr-only {
    display: none !important;
}

#main_page_tabs fieldset {
    display: inline-grid !important;
    grid-template-columns: repeat(3, minmax(120px, 1fr)) !important;
    gap: 4px !important;
    width: min(100%, 520px) !important;
    min-height: 44px !important;
    padding: 4px !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    background: var(--surface-subtle) !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72), var(--shadow) !important;
}

#main_page_tabs label {
    position: relative !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 36px !important;
    padding: 0 16px !important;
    border: 1px solid transparent !important;
    border-radius: 8px !important;
    background: transparent !important;
    color: var(--text-secondary) !important;
    font-weight: 700 !important;
    line-height: 1.2 !important;
    cursor: pointer !important;
    transition: background-color var(--motion), border-color var(--motion), color var(--motion), box-shadow var(--motion) !important;
}

#main_page_tabs input[type="radio"] {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    margin: -1px !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

#main_page_tabs label:hover {
    color: var(--accent) !important;
    background: color-mix(in srgb, var(--accent) 6%, transparent) !important;
}

#main_page_tabs label:has(input[type="radio"]:checked) {
    border-color: var(--accent-border) !important;
    background: var(--surface) !important;
    color: var(--accent) !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), var(--active-ring) !important;
}

#main_page_tabs label:focus-within {
    outline: 2px solid color-mix(in srgb, var(--accent) 55%, transparent) !important;
    outline-offset: 2px !important;
}

#settings_subpage_tabs {
    margin: 0 0 14px !important;
}

#settings_subpage_tabs .wrap,
#settings_subpage_tabs .form {
    border: 0 !important;
    box-shadow: none !important;
    padding: 0 !important;
    background: transparent !important;
}

#settings_subpage_tabs > label,
#settings_subpage_tabs .label-wrap,
#settings_subpage_tabs .block-label,
#settings_subpage_tabs .label,
#settings_subpage_tabs .block-info,
#settings_subpage_tabs .sr-only {
    display: none !important;
}

#settings_subpage_tabs fieldset {
    display: grid !important;
    grid-template-columns: repeat(3, minmax(120px, 1fr)) !important;
    gap: 6px !important;
    padding: 4px !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    background: var(--surface-subtle) !important;
}

#settings_subpage_tabs label {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 36px !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    background: var(--surface) !important;
    color: var(--text-secondary) !important;
    font-weight: 700 !important;
    box-shadow: none !important;
}

#settings_subpage_tabs input[type="radio"] {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

#settings_subpage_tabs label:has(input[type="radio"]:checked) {
    border-color: var(--accent-border-strong) !important;
    background: var(--surface) !important;
    color: var(--accent) !important;
    box-shadow: var(--active-ring) !important;
}

.page-panel {
    padding-top: 12px !important;
}

#workbench_page,
#workbench_page > *,
#main_pages,
#main_pages > * {
    background: var(--bg) !important;
    border-color: transparent !important;
    box-shadow: none !important;
}

.asset-library-tab,
.settings-panel {
    padding: 12px 0 0 !important;
}

.asset-library-tab {
    --block-label-background-fill: var(--surface-subtle) !important;
    --block-label-border-color: var(--border) !important;
    --block-label-text-color: var(--text-label) !important;
    --block-info-text-color: var(--text-label) !important;
}

.asset-library-workspace {
    gap: 14px !important;
}

.asset-library-header {
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) 150px !important;
    gap: 12px !important;
    align-items: stretch !important;
    padding: 14px !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    background: linear-gradient(180deg, var(--surface), var(--surface-subtle)) !important;
    box-shadow: var(--shadow) !important;
}

.asset-summary-box,
.asset-summary-box > *,
.asset-summary-box textarea {
    min-height: 44px !important;
}

.asset-summary-box textarea {
    border-color: var(--accent-border) !important;
    background: var(--accent-soft) !important;
    color: var(--text-card) !important;
    font-weight: 650 !important;
}

.asset-library-header .primary-btn,
.asset-library-header .primary-btn button {
    height: 44px !important;
    min-width: 142px !important;
    align-self: end !important;
}

.asset-library-grid {
    display: grid !important;
    grid-template-columns: minmax(280px, 0.9fr) minmax(460px, 1.8fr) minmax(340px, 1fr) !important;
    gap: 14px !important;
    align-items: stretch !important;
}

.asset-panel {
    min-height: 560px !important;
    padding: 14px !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    background: var(--surface) !important;
    box-shadow: var(--shadow) !important;
}

.asset-panel-title {
    color: var(--text-title);
    font-size: 15px;
    font-weight: 760;
    line-height: 1.25;
}

.asset-panel-subtitle {
    margin: 4px 0 12px;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.45;
}

.asset-type-switch fieldset {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 6px !important;
    padding: 4px !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    background: var(--surface-subtle) !important;
}

.asset-type-switch label {
    justify-content: center !important;
    min-height: 34px !important;
    border: 1px solid transparent !important;
    border-radius: 8px !important;
    background: transparent !important;
    color: var(--text-secondary) !important;
    font-weight: 700 !important;
}

.asset-type-switch label:has(input[type="radio"]:checked) {
    border-color: var(--accent-border) !important;
    background: var(--surface) !important;
    color: var(--accent) !important;
    box-shadow: var(--active-ring) !important;
}

.asset-upload-dropzone {
    margin-top: 10px !important;
}

.asset-library-tab :where(.block-label, .label-wrap, .block-info, .gallery-label, .file-preview) {
    border-color: var(--border) !important;
    background: var(--surface-subtle) !important;
    background-color: var(--surface-subtle) !important;
    color: var(--text-label) !important;
    box-shadow: none !important;
}

.asset-library-tab :where(.block-label, .label-wrap, .block-info, .gallery-label, .file-preview) * {
    background: transparent !important;
    background-color: transparent !important;
    color: var(--text-label) !important;
}

.asset-upload-dropzone .block-label,
.asset-upload-dropzone .label-wrap,
.asset-preview-panel .block-label,
.asset-preview-panel .label-wrap {
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    background: var(--surface-subtle) !important;
    color: var(--text-label) !important;
    box-shadow: none !important;
}

.asset-upload-dropzone .block-label span,
.asset-upload-dropzone .label-wrap span,
.asset-preview-panel .block-label span,
.asset-preview-panel .label-wrap span {
    color: var(--text-label) !important;
}

.asset-upload-dropzone :where(.block-label, .label-wrap, .block-info, .file-preview),
.asset-preview-panel :where(.block-label, .label-wrap, .block-info, .gallery-label) {
    background-color: var(--surface-subtle) !important;
    color: var(--text-label) !important;
}

.asset-upload-dropzone :where(.block-label, .label-wrap, .block-info, .gallery-label, .file-preview) *,
.asset-preview-panel :where(.block-label, .label-wrap, .block-info, .gallery-label, .file-preview) * {
    background-color: transparent !important;
    color: var(--text-label) !important;
}

.asset-upload-dropzone [data-testid="file"] {
    min-height: 190px !important;
    border: 1px dashed var(--accent-border-strong) !important;
    border-radius: var(--radius) !important;
    background: color-mix(in srgb, var(--accent-soft) 52%, var(--surface)) !important;
}

.asset-upload-panel input,
.asset-preview-panel input,
.asset-meta-panel input,
.asset-meta-panel textarea {
    background: var(--surface-subtle) !important;
}

.asset-text-field :where(input, textarea) {
    min-height: 42px !important;
    border-color: var(--border) !important;
    background: var(--surface-subtle) !important;
    color: var(--text-card) !important;
    caret-color: var(--accent) !important;
    box-shadow: none !important;
}

.asset-id-field :where(input, textarea) {
    font-family: var(--font-mono) !important;
    font-size: 13px !important;
}

.asset-text-field :where(input, textarea):focus,
.asset-text-field :where(input, textarea):focus-visible,
.asset-path-box :where(input, textarea):focus,
.asset-path-box :where(input, textarea):focus-visible {
    outline: 2px solid color-mix(in srgb, var(--accent) 42%, transparent) !important;
    outline-offset: 1px !important;
    border-color: var(--accent-border-strong) !important;
    box-shadow: 0 0 0 3px var(--focus) !important;
}

.asset-library-tab :where(.asset-text-field, .asset-path-box) :where([role="tooltip"], .tooltip, .info, .popup, .popover) {
    display: none !important;
}

.asset-path-box textarea,
.asset-path-box input {
    min-height: 48px !important;
    border-color: var(--accent-border) !important;
    background: var(--accent-soft) !important;
    color: var(--text-card) !important;
    font-family: var(--font-mono) !important;
    font-size: 12px !important;
}

.asset-preview-panel {
    background:
        linear-gradient(90deg, rgba(148, 163, 184, 0.08) 1px, transparent 1px),
        linear-gradient(180deg, rgba(148, 163, 184, 0.08) 1px, transparent 1px),
        var(--surface) !important;
    background-size: 28px 28px !important;
}

.asset-preview-tools {
    display: grid !important;
    grid-template-columns: minmax(220px, 0.8fr) minmax(280px, 1.2fr) !important;
    gap: 10px !important;
    align-items: end !important;
}

.asset-existing-file,
.asset-existing-path {
    min-width: 0 !important;
}

.asset-existing-file [data-testid="file"] {
    min-height: 78px !important;
    border: 1px dashed var(--accent-border) !important;
    border-radius: 10px !important;
    background: var(--surface-subtle) !important;
}

.asset-preview-gallery {
    height: 560px !important;
    min-height: 560px !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    background: color-mix(in srgb, var(--surface) 82%, transparent) !important;
    overflow: hidden !important;
}

.asset-preview-gallery :where(.wrap, .image-container, .empty, .empty\\!, .preview, .container) {
    height: 100% !important;
    min-height: 100% !important;
    border: 0 !important;
    background: transparent !important;
}

.asset-preview-gallery img {
    width: 100% !important;
    height: 100% !important;
    max-height: 100% !important;
    object-fit: contain !important;
}

.asset-meta-panel {
    gap: 8px !important;
}

.asset-meta-panel .quick-btn,
.asset-meta-panel .quick-btn button {
    min-height: 42px !important;
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: var(--accent-foreground) !important;
}

.asset-library-log textarea {
    min-height: 112px !important;
    border-color: var(--border) !important;
    background: var(--surface-muted) !important;
    font-family: var(--font-mono) !important;
    font-size: 12px !important;
}

.episode-strip {
    width: 300px;
    min-height: 34px;
    padding: 4px 8px;
    border-radius: 14px;
    border: 1px solid var(--accent-border);
    background: color-mix(in srgb, var(--surface) 88%, transparent);
    box-shadow: var(--shadow-xs);
    overflow-x: auto;
    overflow-y: hidden;
}

.episode-grid {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: max-content;
}

.episode-cell {
    min-width: 54px !important;
    height: 30px !important;
    min-height: 30px !important;
    padding: 0 12px !important;
    border: 1px solid var(--border) !important;
    border-radius: 9px !important;
    background: var(--surface-muted) !important;
    color: var(--text-card) !important;
    cursor: pointer !important;
    font-weight: 800 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    opacity: 1 !important;
    white-space: nowrap !important;
    transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease, transform 120ms ease, box-shadow 120ms ease !important;
}

.episode-cell b {
    color: inherit !important;
    font-size: 13px !important;
    line-height: 1 !important;
    opacity: 1 !important;
}

.episode-cell.active,
.episode-cell:hover {
    border-color: var(--accent-border-strong) !important;
    background: var(--accent-soft) !important;
    color: var(--accent-hover) !important;
}

.episode-cell.active {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: #ffffff !important;
    box-shadow: 0 0 0 2px var(--accent-soft), var(--shadow-xs) !important;
}

.episode-cell:active {
    transform: translateY(1px) scale(0.98) !important;
}

/* Layout */
.app-shell {
    gap: 16px !important;
    align-items: stretch !important;
    flex-wrap: nowrap !important;
    padding: 0 !important;
    border: 0 !important;
    background: var(--bg) !important;
    box-shadow: none !important;
}

.app-shell,
.app-shell > *,
.app-shell > * > *,
.app-shell > * > * > * {
    border-color: transparent !important;
    background-color: var(--bg) !important;
    box-shadow: none !important;
}

.app-shell > .sidebar-column {
    flex: 0 0 300px !important;
    max-width: 340px !important;
}

.app-shell > .workspace-column {
    flex: 1.35 1 880px !important;
    min-width: 640px !important;
}

.app-shell > .output-column {
    flex: 0.9 1 560px !important;
    min-width: 460px !important;
}

.sidebar-column,
.workspace-column,
.output-column {
    gap: 14px !important;
    background: var(--bg) !important;
    border: 0 !important;
    box-shadow: none !important;
}

.output-column {
    position: sticky;
    top: 12px;
    align-self: stretch !important;
    max-height: none;
    overflow: visible;
    padding-right: 2px;
}

.output-column .right-panel {
    min-height: var(--aicf-segments-panel-min-height, 720px);
    box-sizing: border-box;
}

.output-column::-webkit-scrollbar,
.right-panel::-webkit-scrollbar {
    width: 8px;
}

.output-column::-webkit-scrollbar-thumb,
.right-panel::-webkit-scrollbar-thumb {
    background: var(--scrollbar-thumb);
    border-radius: 999px;
}

/* Gradio emits many wrapper elements for rows, columns, forms and blocks.
   Treat those wrappers as layout only; named panels below provide the cards. */
.gradio-container :where(.form, .block, .panel, .gr-box, .gr-group),
.gradio-container :where(.row, .column, .compact),
#main_pages,
#main_pages > *,
.page-panel {
    border: 0 !important;
    outline: 0 !important;
    background: transparent !important;
    background-color: transparent !important;
    box-shadow: none !important;
}

.gradio-container :where(.form, .block, .panel, .gr-box, .gr-group)::before,
.gradio-container :where(.form, .block, .panel, .gr-box, .gr-group)::after {
    border-color: transparent !important;
    background: transparent !important;
    box-shadow: none !important;
}

/* Surfaces */
.app-header,
.workspace-card,
.side-card,
.right-panel,
.output-card,
.asset-library-header,
.asset-panel,
.theme-switcher,
.dev-tabs,
.group-card,
.metric-card,
.prompt-preview,
.final-output-line,
.empty-card {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    background: var(--surface) !important;
    box-shadow: var(--shadow) !important;
}

.workspace-card,
.side-card,
.right-panel,
.output-card {
    padding: 16px !important;
}

.workspace-card:hover,
.side-card:hover,
.right-panel:hover,
.group-card:hover {
    border-color: var(--border-strong) !important;
    box-shadow: var(--shadow) !important;
}

.side-card {
    margin-bottom: 0 !important;
}

#official_platform_section,
#official_platform_section > .html-container,
#official_platform_section > .html-container > .prose {
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* The Blocks root already contributes a 16px vertical gap. Keep this
   disclosure visually attached to the header with one compact 8px beat. */
#official_platform_section {
    margin-top: -8px !important;
}

.official-platform-disclosure {
    margin: 0;
}

.official-platform-disclosure > summary {
    min-height: 38px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    color: var(--text-title);
    cursor: pointer;
    list-style: none;
    user-select: none;
}

.official-platform-disclosure > summary::-webkit-details-marker {
    display: none;
}

.official-platform-disclosure > summary:hover {
    border-color: var(--border-strong);
    background: var(--surface-subtle);
}

.official-platform-disclosure > summary:focus-visible {
    outline: 3px solid var(--focus);
    outline-offset: 2px;
}

.official-platform-summary-copy {
    min-width: 0;
    display: flex;
    align-items: baseline;
    gap: 10px;
}

.official-platform-summary-copy strong {
    font-size: 14px;
}

.official-platform-summary-copy small {
    color: var(--text-muted);
    font-size: 11px;
}

.official-platform-chevron {
    width: 9px;
    height: 9px;
    flex: 0 0 9px;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    transform: rotate(45deg);
    transition: transform .18s ease;
}

.official-platform-disclosure[open] .official-platform-chevron {
    transform: rotate(225deg);
}

.official-platform-area {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 20px;
    margin: 8px 0 0 !important;
}

.official-platform-warning {
    grid-template-columns: minmax(0, 1fr);
}

.account-card {
    min-height: 248px;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--surface);
    box-shadow: var(--shadow-hover);
    box-sizing: border-box;
}

.account-card-warning {
    border-color: var(--warning-border);
    background: var(--warning-soft);
}

.account-icon {
    width: 48px;
    height: 48px;
    flex: 0 0 48px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    border: 1px solid var(--accent-border);
    background: var(--accent-soft);
    color: var(--accent);
    font-size: 14px;
    font-weight: 800;
    line-height: 1;
}

.account-card-title {
    width: 100%;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
    color: var(--text-title);
    font-size: 18px;
    font-weight: 700;
    line-height: 1.3;
}

.account-card-body {
    width: 100%;
    display: grid;
    grid-template-columns: 144px minmax(0, 1fr);
    gap: 16px;
    align-items: center;
    padding: 12px 0 0;
}

.account-visual {
    width: 144px;
    max-width: 100%;
    min-height: 174px;
    display: flex;
    align-items: center;
    justify-content: center;
    justify-self: start;
    border-radius: 16px;
    background: var(--surface-subtle);
    border: 1px solid var(--border);
}

.account-qr-wrap {
    width: 144px;
    min-height: 174px;
}

.account-visual .account-icon {
    width: 96px;
    height: 96px;
    flex-basis: 96px;
    font-size: 24px;
    border-width: 1px;
}

.account-qr {
    width: 144px;
    height: auto;
    max-height: 174px;
    max-width: 100%;
    object-fit: contain;
    border-radius: 12px;
    display: block;
    cursor: zoom-in;
    transition: transform .18s ease, box-shadow .18s ease;
}

.account-qr:hover {
    transform: translateY(-1px);
    box-shadow: 0 10px 22px rgba(30,100,220,.12);
}

.account-gift-wrap {
    color: var(--accent);
}

.account-gift-icon {
    width: 96px;
    height: 96px;
    fill: var(--accent-soft);
    stroke: var(--accent);
    stroke-width: 5;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.account-content {
    min-width: 0;
    align-self: center;
}

.account-card-body .account-content {
    min-height: 174px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}

.account-title,
.account-name {
    color: var(--text-title);
    font-size: 24px;
    font-weight: 700;
    line-height: 1.25;
    margin-bottom: 8px;
}

.account-row {
    color: var(--text-secondary);
    font-size: 16px;
    line-height: 1.5;
}

.account-desc {
    margin-top: 6px;
    color: var(--text-secondary);
    font-size: 14px;
    line-height: 1.6;
}

.account-row b {
    color: var(--accent) !important;
    font-weight: 500;
}

.account-row span {
    color: var(--text-secondary);
}

.account-card-footer {
    width: 100%;
    display: flex;
    justify-content: flex-start;
    padding-top: 0;
}

.account-cta {
    min-width: 112px;
    height: 40px;
    padding: 0 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    background: var(--accent);
    color: var(--accent-foreground) !important;
    font-size: 14px;
    font-weight: 700;
    line-height: 1;
    text-decoration: none !important;
    box-shadow: 0 8px 18px rgba(47,128,237,.18);
    transition: transform .18s ease, box-shadow .18s ease;
}

.account-cta:hover {
    transform: translateY(-2px);
    box-shadow: 0 12px 24px rgba(47,128,237,.24);
}

@media (max-width: 1300px) {
    .official-platform-area {
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 16px;
    }

    .account-card {
        min-height: 248px;
        padding: 14px 16px;
    }

    .account-card-body {
        grid-template-columns: 144px minmax(0, 1fr);
        gap: 16px;
    }

    .account-visual {
        justify-self: start;
    }
}

@media (max-width: 900px) {
    .official-platform-area {
        grid-template-columns: 1fr;
        gap: 12px;
    }

    .account-card {
        min-height: 0;
        padding: 18px;
    }

    .account-card-body {
        grid-template-columns: 144px minmax(0, 1fr);
    }

    .account-qr-wrap {
        width: 144px;
        min-height: 174px;
    }

    .account-qr {
        width: 144px;
        max-height: 174px;
    }
}

@media (max-width: 560px) {
    .account-card-body {
        grid-template-columns: 1fr;
    }

    .account-content {
        min-height: 0;
        gap: 16px;
    }
}

.hero-card {
    overflow: hidden;
}

/* Typography */
.section-title {
    color: var(--text-title) !important;
    font-size: 19px !important;
    font-weight: 700 !important;
    line-height: 1.3 !important;
    letter-spacing: 0 !important;
}

.side-title,
.panel-title,
.segment-title {
    color: var(--text-card) !important;
    font-size: 14px !important;
    font-weight: 680 !important;
    line-height: 1.35 !important;
    letter-spacing: 0 !important;
}

.section-kicker,
.segment-id {
    color: var(--text-label) !important;
    font-size: 11px !important;
    font-weight: 650 !important;
    line-height: 1.2 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase;
}

.section-subtitle,
.side-copy,
.panel-subtitle,
.key-img-preview-info,
.empty-card,
.prompt-preview {
    color: var(--text-secondary) !important;
    font-size: 12.5px !important;
    font-weight: 400 !important;
    line-height: 1.45 !important;
}

.metric-card b,
.path-line {
    color: var(--text-body) !important;
}

.path-line b,
.final-output-line b {
    color: var(--text-card);
    font-weight: 600;
}

label,
.label-wrap span,
.topic-row label,
.topic-row .label-wrap span,
.workspace-column label,
.workspace-column .label-wrap span,
.metric-card span {
    color: var(--text-label) !important;
    font-size: 12px !important;
    font-weight: 600 !important;
}

.gradio-container :where(.block-label, .label-wrap, .block-info, .gallery-label, .file-preview) {
    border-color: var(--border) !important;
    background: var(--surface-subtle) !important;
    background-color: var(--surface-subtle) !important;
    color: var(--text-label) !important;
    box-shadow: none !important;
}

.gradio-container :where(.block-label, .label-wrap, .block-info, .gallery-label, .file-preview) * {
    background: transparent !important;
    background-color: transparent !important;
    color: var(--text-label) !important;
    fill: currentColor !important;
    stroke: currentColor !important;
}

/* Buttons */
button,
button.gr-button,
.gr-button,
.card-regen-btn,
.dev-tab-btn,
.app-header .gear-icon,
.app-header .open-videos-dir-btn,
.app-header .preview-final-btn {
    min-height: 36px !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--surface) !important;
    color: var(--text-card) !important;
    box-shadow: none !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    line-height: 1.2 !important;
    letter-spacing: 0 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    vertical-align: middle !important;
    cursor: pointer !important;
    transition: background-color var(--motion), border-color var(--motion), color var(--motion), box-shadow var(--motion), opacity var(--motion) !important;
    transform: none !important;
    filter: none !important;
    touch-action: manipulation;
}

button:hover,
button.gr-button:hover,
.gr-button:hover,
.card-regen-btn:hover,
.dev-tab-btn:hover,
.app-header .gear-icon:hover,
.app-header .open-videos-dir-btn:hover,
.app-header .preview-final-btn:hover {
    border-color: var(--border-strong) !important;
    background: var(--surface-subtle) !important;
    color: var(--text-title) !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
    transform: none !important;
}

.app-header .open-videos-dir-btn,
.app-header .open-videos-dir-btn:hover {
    width: 38px !important;
    min-width: 38px !important;
    padding: 0 !important;
    border-color: var(--accent) !important;
    background: var(--accent) !important;
    color: var(--accent-foreground) !important;
    font-weight: 700 !important;
    white-space: nowrap;
}

.app-header .open-videos-dir-btn:hover {
    border-color: var(--accent-hover) !important;
    background: var(--accent-hover) !important;
    color: var(--accent-foreground) !important;
    box-shadow: 0 2px 5px rgba(37, 99, 235, 0.22) !important;
}

.app-header .open-videos-dir-btn svg {
    width: 18px;
    height: 18px;
    stroke: currentColor;
}

.app-header .open-videos-dir-btn:focus-visible {
    outline: 0;
    box-shadow: 0 0 0 3px var(--focus) !important;
}

button:active,
button.gr-button:active,
.gr-button:active,
.card-regen-btn:active {
    background: var(--surface-muted) !important;
    transform: none !important;
    box-shadow: none !important;
}

button:disabled,
.gr-button:disabled {
    cursor: not-allowed !important;
    opacity: 0.5 !important;
    background: var(--surface-muted) !important;
    color: var(--text-disabled) !important;
}

body.archive-readonly #story_editor textarea,
body.archive-readonly #beats_editor textarea,
body.archive-readonly .segment-director-editor,
body.archive-readonly .aicf-codemirror .CodeMirror {
    background: var(--surface-subtle) !important;
    color: var(--text-body) !important;
    cursor: default !important;
}

body.archive-readonly button.archive-disabled,
body.archive-readonly button.archive-disabled:hover {
    opacity: 0.48 !important;
    cursor: not-allowed !important;
    border-color: var(--border) !important;
    background: var(--surface-muted) !important;
    color: var(--text-secondary) !important;
    box-shadow: none !important;
}

.one-click-main,
.one-click-main button,
button.one-click-main {
    border-color: var(--accent) !important;
    background: var(--accent) !important;
    color: var(--accent-foreground) !important;
    box-shadow: 0 1px 2px rgba(37, 99, 235, 0.18) !important;
}

.one-click-main:hover,
.one-click-main button:hover,
button.one-click-main:hover {
    border-color: var(--accent-hover) !important;
    background: var(--accent-hover) !important;
    color: var(--accent-foreground) !important;
}

.quick-blue,
.quick-blue button,
button.quick-blue {
    border-color: var(--accent-border) !important;
    background: color-mix(in srgb, var(--accent-soft) 68%, var(--surface)) !important;
    color: var(--accent-hover) !important;
}

.quick-blue:hover,
.quick-blue button:hover,
button.quick-blue:hover {
    border-color: var(--accent-border-strong) !important;
    background: var(--accent-soft-hover) !important;
    color: var(--accent-hover) !important;
}

.ghost-btn,
.ghost-btn button,
button.ghost-btn {
    background: transparent !important;
    color: var(--text-secondary) !important;
}

/* Forms and editors */
input:not([type="checkbox"]):not([type="radio"]),
textarea,
select,
.cm-editor,
.cm-gutters,
.wrap input:not([type="checkbox"]):not([type="radio"]),
.wrap textarea {
    min-height: 38px !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--surface) !important;
    color: var(--text-body) !important;
    box-shadow: none !important;
    font-size: 13px !important;
    line-height: 1.5 !important;
}

input:not([type="checkbox"]):not([type="radio"])::placeholder,
textarea::placeholder,
.wrap input:not([type="checkbox"]):not([type="radio"])::placeholder,
.wrap textarea::placeholder {
    color: var(--text-placeholder) !important;
    opacity: 1 !important;
}

input:not([type="checkbox"]):not([type="radio"]):focus,
textarea:focus,
select:focus,
.cm-editor.cm-focused,
button:focus-visible,
button.gr-button:focus-visible,
.gr-button:focus-visible {
    outline: 3px solid var(--focus) !important;
    outline-offset: 1px !important;
    border-color: var(--accent-border-strong) !important;
    box-shadow: none !important;
}

input:not([type="checkbox"]):not([type="radio"]):disabled,
textarea:disabled,
select:disabled {
    color: var(--text-disabled) !important;
    background: var(--surface-muted) !important;
}

.gradio-container :where(.dropdown, .select, .multiselect, .wrap-inner, .secondary-wrap, .input-container) {
    border-color: var(--border) !important;
    background: var(--surface) !important;
    color: var(--text-body) !important;
    box-shadow: none !important;
}

.gradio-container :where(.token, .token-container, .selected-item, .selected-option, .chip) {
    border: 1px solid var(--accent-border) !important;
    border-radius: 8px !important;
    background: color-mix(in srgb, var(--accent-soft) 68%, var(--surface)) !important;
    color: var(--text-card) !important;
    box-shadow: none !important;
}

.gradio-container :where(.token span, .selected-item span, .selected-option span, .chip span) {
    color: var(--text-card) !important;
}

.gradio-container :where(.token button, .token-remove, .chip button) {
    color: var(--text-secondary) !important;
    background: transparent !important;
    border: 0 !important;
}

.gradio-container :where(.dropdown-arrow, .icon-wrap, .secondary-wrap svg) {
    color: var(--text-secondary) !important;
    fill: var(--text-secondary) !important;
}

.sidebar-column :where([aria-label*="Remove"], [aria-label*="remove"], [title*="Remove"], [title*="remove"]) {
    background: transparent !important;
    color: var(--text-secondary) !important;
    border: 0 !important;
}

.sidebar-column :where(.wrap, .form, .block) :where(div, span):has(> [aria-label*="Remove"]),
.sidebar-column :where(.wrap, .form, .block) :where(div, span):has(> [title*="Remove"]) {
    border-color: var(--accent-border) !important;
    background: color-mix(in srgb, var(--accent-soft) 68%, var(--surface)) !important;
    color: var(--text-card) !important;
}

.topic-row {
    align-items: stretch !important;
    gap: 18px !important;
    margin-top: 14px !important;
    padding-top: 0 !important;
}

.topic-row > *,
.topic-row .wrap,
.topic-row .form {
    min-width: 0 !important;
}

.topic-row textarea,
.topic-row input:not([type="checkbox"]):not([type="radio"]),
.topic-row select {
    background: var(--surface-subtle) !important;
}

.duration-beat-stack {
    flex: 0 0 250px !important;
    width: 250px !important;
    min-width: 250px !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    gap: 10px !important;
    margin: 0 !important;
    padding-left: 18px !important;
    border-left: 1px solid color-mix(in srgb, var(--accent-border) 60%, transparent) !important;
}

.duration-beat-stack > * {
    width: 100% !important;
    min-width: 0 !important;
    margin: 0 !important;
}

.duration-beat-stack .compact-field-row {
    display: grid !important;
    grid-template-columns: 96px 120px !important;
    align-items: center !important;
    height: 36px !important;
    min-height: 36px !important;
    max-height: 36px !important;
    gap: 12px !important;
    width: 228px !important;
    min-width: 228px !important;
    margin: 0 !important;
    padding: 0 !important;
}

.duration-beat-stack .compact-field-row.hide {
    display: none !important;
}

.duration-beat-stack .compact-field-row > *,
.duration-beat-stack .compact-field-row .wrap,
.duration-beat-stack .compact-field-row .form {
    height: 36px !important;
    min-height: 36px !important;
    max-height: 36px !important;
    min-width: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}

.duration-beat-stack .compact-field-row > .form {
    width: 120px !important;
    min-width: 120px !important;
    overflow: visible !important;
}

.duration-beat-stack .compact-field-row > .block:not(.compact-field-input),
.duration-beat-stack .compact-field-row .html-container,
.duration-beat-stack .compact-field-row .html-container .prose {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    height: 36px !important;
    min-height: 36px !important;
    margin: 0 !important;
    padding: 0 !important;
}

.duration-beat-stack .compact-field-label {
    width: 96px !important;
    min-width: 96px !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    color: var(--text-secondary) !important;
    font-size: 12px !important;
    font-weight: 700 !important;
    line-height: 36px !important;
    text-align: right !important;
    white-space: nowrap !important;
}

.duration-beat-stack .compact-field-input,
.duration-beat-stack .compact-field-input > *,
.duration-beat-stack .compact-field-input .wrap,
.duration-beat-stack .compact-field-input .form,
.duration-beat-stack .compact-field-input label,
.duration-beat-stack .compact-field-input .input-container {
    display: block !important;
    width: 120px !important;
    height: 36px !important;
    min-height: 36px !important;
    min-width: 120px !important;
    margin: 0 !important;
    padding: 0 !important;
}

.duration-beat-stack .compact-field-input .label-wrap,
.duration-beat-stack .compact-field-input .block-label {
    display: none !important;
}

.duration-beat-stack .compact-field-input input:not([type="checkbox"]):not([type="radio"]),
.duration-beat-stack .compact-field-input select,
.duration-beat-stack .compact-field-input textarea {
    width: 120px !important;
    height: 36px !important;
    min-height: 36px !important;
    max-height: 36px !important;
    margin: 0 !important;
    border-radius: 6px !important;
    border-color: var(--accent-border-strong) !important;
    background: color-mix(in srgb, var(--surface-subtle) 92%, var(--accent-soft)) !important;
    color: var(--text-card) !important;
    resize: none !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
    padding: 6px 10px !important;
    line-height: 22px !important;
}

.duration-beat-stack .beat-count-control input,
.duration-beat-stack .beat-count-control textarea,
.duration-beat-stack .custom-duration-control input {
    text-align: center !important;
    font-weight: 700 !important;
}

.duration-beat-stack .target-beat-count-proxy {
    display: block !important;
    width: 120px !important;
    height: 36px !important;
    min-height: 36px !important;
    margin: 0 !important;
    padding: 6px 10px !important;
    box-sizing: border-box !important;
    border: 1px solid var(--accent-border-strong) !important;
    border-radius: 6px !important;
    background: color-mix(in srgb, var(--surface-subtle) 92%, var(--accent-soft)) !important;
    color: var(--text-card) !important;
    font: inherit !important;
    text-align: center !important;
    font-weight: 700 !important;
    outline: none !important;
}

.duration-beat-stack .target-beat-count-proxy:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 20%, transparent) !important;
}

.target-beat-count-hidden {
    display: none !important;
}

.beat-risk-row {
    align-items: flex-end !important;
    gap: 14px !important;
}

.beat-risk-row .beat-split-index,
#complex_beat_index {
    flex: 0 0 188px !important;
    width: 188px !important;
    min-width: 188px !important;
}

/* Gradio places Number inside a flex .form wrapper; reserve space there too. */
.beat-risk-row > .form:has(#complex_beat_index) {
    flex: 0 0 188px !important;
    width: 188px !important;
    min-width: 188px !important;
}

.beat-risk-row .beat-split-index input,
#complex_beat_index input {
    width: 100% !important;
}

.beat-risk-row .beat-risk-detect-btn,
.beat-risk-row .beat-split-submit-btn,
.beat-risk-row .beat-add-submit-btn,
.beat-risk-row .beat-delete-submit-btn {
    flex: 0 0 auto !important;
    min-width: 156px !important;
}

.beat-risk-row .beat-delete-submit-btn button,
.beat-risk-row .beat-delete-submit-btn.gr-button,
.beat-risk-row .beat-delete-submit-btn {
    border-color: color-mix(in srgb, #ef4444 52%, var(--border)) !important;
    color: #b91c1c !important;
    background: color-mix(in srgb, #fee2e2 76%, var(--surface)) !important;
}

.beat-risk-row .beat-add-submit-btn button,
.beat-risk-row .beat-add-submit-btn.gr-button,
.beat-risk-row .beat-add-submit-btn {
    border-color: color-mix(in srgb, #22c55e 52%, var(--border)) !important;
    color: #15803d !important;
    background: color-mix(in srgb, #dcfce7 76%, var(--surface)) !important;
}

.beat-risk-row > *,
.beat-risk-row .form,
.beat-risk-row .wrap {
    min-width: 0 !important;
}

.beat-risk-row button,
.beat-risk-row button.gr-button,
.beat-risk-row .gr-button {
    height: 38px !important;
    min-height: 38px !important;
    margin: 0 !important;
    padding: 0 14px !important;
    line-height: 1 !important;
}

.beat-risk-row input:not([type="checkbox"]):not([type="radio"]) {
    height: 38px !important;
    min-height: 38px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    color: var(--text-title) !important;
}

#segment_pager.segment-pager {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 8px !important;
    margin: 10px 0 2px !important;
}

#segment_pager .pager-btn {
    flex: 0 0 auto !important;
    min-width: 74px !important;
    height: 36px !important;
    min-height: 36px !important;
    padding: 0 12px !important;
}

#segment_pager .pager-label {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-width: 96px !important;
    height: 36px !important;
    padding: 0 10px !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    background: var(--surface) !important;
    color: var(--text-title) !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    font-variant-numeric: tabular-nums;
}

@media (max-width: 720px) {
    .beat-risk-row {
        align-items: stretch !important;
    }

    .beat-risk-row .beat-split-index,
    #complex_beat_index,
    .beat-risk-row > .form:has(#complex_beat_index),
    .beat-risk-row .beat-risk-detect-btn,
    .beat-risk-row .beat-split-submit-btn,
    .beat-risk-row .beat-add-submit-btn,
    .beat-risk-row .beat-delete-submit-btn {
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 100% !important;
    }
}

input[type="checkbox"],
input[type="radio"] {
    width: 16px !important;
    min-width: 16px !important;
    height: 16px !important;
    min-height: 16px !important;
    margin: 0 8px 0 0 !important;
    padding: 0 !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 4px !important;
    background: var(--surface) !important;
    box-shadow: none !important;
    accent-color: var(--accent);
    cursor: pointer;
    vertical-align: middle;
    pointer-events: auto !important;
    appearance: auto !important;
    -webkit-appearance: auto !important;
}

input[type="radio"] {
    border-radius: 999px !important;
}

input[type="checkbox"]:focus,
input[type="radio"]:focus {
    outline: none !important;
    box-shadow: 0 0 0 3px var(--focus) !important;
}

input[type="checkbox"]:focus:not(:focus-visible),
input[type="radio"]:focus:not(:focus-visible) {
    outline: none !important;
    box-shadow: none !important;
}

input[type="checkbox"]:focus-visible,
input[type="radio"]:focus-visible {
    outline: none !important;
    box-shadow: 0 0 0 3px var(--focus) !important;
}

input[type="checkbox"]:disabled,
input[type="radio"]:disabled {
    cursor: not-allowed;
    opacity: 0.5;
}

#dev-tab3 label,
#dev-tab3 .label-wrap span,
#dev-tab3 .wrap label,
#dev-tab3 span {
    color: var(--text-body) !important;
    opacity: 1 !important;
}

#dev-tab3 label {
    cursor: pointer !important;
}

#dev-tab3 label,
#dev-tab3 label:hover,
#dev-tab3 label:active,
#dev-tab3 label:focus,
#dev-tab3 label:focus-within,
#dev-tab3 .wrap,
#dev-tab3 .wrap:hover,
#dev-tab3 .wrap:active,
#dev-tab3 .wrap:focus-within,
#dev-tab3 .form,
#dev-tab3 .form:hover,
#dev-tab3 .form:active,
#dev-tab3 .form:focus-within,
#dev-tab3 .block,
#dev-tab3 .block:hover,
#dev-tab3 .block:active,
#dev-tab3 .block:focus-within,
#dev-tab3 .selected,
#dev-tab3 .checked {
    background: transparent !important;
    background-color: transparent !important;
    box-shadow: none !important;
    outline: none !important;
}

#dev-tab3 *::before,
#dev-tab3 *::after {
    box-shadow: none !important;
}

#dev-tab3 :where(div, label, span):has(input[type="checkbox"]),
#dev-tab3 :where(div, label, span):has(input[type="radio"]) {
    background: transparent !important;
    background-color: transparent !important;
    box-shadow: none !important;
    outline: none !important;
}

#dev-tab3 input[type="checkbox"],
#dev-tab3 input[type="radio"] {
    opacity: 1 !important;
    cursor: pointer !important;
}

.main-action-row {
    display: grid !important;
    grid-template-columns: repeat(5, minmax(112px, 1fr)) !important;
    gap: 9px !important;
    align-items: stretch !important;
    margin: 12px 0 0 !important;
}

.main-action-row > *,
.main-action-row .form,
.main-action-row .wrap {
    min-width: 0 !important;
    align-self: stretch !important;
}

.main-action-row button,
.main-action-row button.gr-button,
.main-action-row .gr-button {
    width: 100% !important;
    height: 38px !important;
    min-height: 36px !important;
    padding: 0 10px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    white-space: normal !important;
    text-align: center !important;
}

.one-click-action-row {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    margin: 32px 0 0 !important;
}

.one-click-action-row > *,
.one-click-action-row .one-click-main {
    width: min(360px, 100%) !important;
    max-width: 360px !important;
}

#minimal_mode_box,
#minimal_mode_box label {
    margin: 0 !important;
}

#story_editor textarea,
#beats_editor textarea,
.workspace-column .cm-editor {
    width: 100% !important;
    max-width: none !important;
    box-sizing: border-box !important;
    font-family: var(--font-mono) !important;
    font-size: 12.5px !important;
    line-height: 1.64 !important;
}

#story_editor .generating,
#story_editor .progress-text,
#story_editor [data-testid="block-info"],
#beats_editor .generating,
#beats_editor .progress-text,
#beats_editor [data-testid="block-info"] {
    display: none !important;
}

#story_editor textarea {
    width: 100% !important;
    max-width: none !important;
    min-height: 260px !important;
    padding: 12px !important;
}

#beats_editor textarea {
    width: 100% !important;
    max-width: none !important;
    min-height: 420px !important;
    padding: 12px !important;
}

.quality-highlight-host {
    position: relative !important;
}

.quality-editor-body {
    position: relative !important;
    width: 100% !important;
    max-width: none !important;
    min-width: 0 !important;
}

.quality-risk-summary {
    display: none;
    margin: 0 0 0 auto !important;
    padding: 0 !important;
    border: 0 !important;
    background: transparent !important;
    color: var(--warning-text) !important;
    box-shadow: none !important;
    position: relative !important;
    font-size: 12px;
    line-height: 1.5;
    font-weight: 650;
}

.quality-risk-note-item {
    display: grid;
    grid-template-columns: auto minmax(70px, auto) 1fr;
    gap: 8px;
    align-items: start;
    color: var(--text-body);
    font-size: 12px;
    line-height: 1.55;
}

.quality-risk-note-item strong {
    margin-right: 0;
    color: var(--warning-text);
    font-weight: 700;
}

.quality-risk-phrase {
    color: var(--text-card);
    font-weight: 650;
}

.quality-risk-reason {
    color: var(--text-secondary);
}

.quality-editor-shell {
    display: flex !important;
    flex-direction: column !important;
    gap: 8px !important;
}

.quality-editor-shell .label-wrap {
    display: none !important;
}

.quality-editor-header {
    display: flex !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 12px !important;
    min-height: 28px !important;
    padding: 0 2px !important;
}

.quality-risk-badge {
    display: inline-flex !important;
    align-items: center !important;
    gap: 6px !important;
    padding: 4px 9px !important;
    border: 1px solid var(--warning-border) !important;
    border-radius: 999px !important;
    background: var(--warning-soft) !important;
    color: var(--warning-text) !important;
    cursor: pointer !important;
}

.quality-risk-detail {
    display: none;
    position: absolute;
    right: 0;
    top: calc(100% + 8px);
    z-index: 8;
    width: min(560px, calc(100vw - 72px));
    max-height: 220px;
    overflow: auto;
    padding: 10px;
    border: 1px solid var(--warning-border);
    border-radius: var(--radius);
    background: var(--surface);
    box-shadow: var(--shadow-overlay);
}

.quality-risk-summary.is-expanded .quality-risk-detail {
    display: grid;
    gap: 8px;
}

.quality-highlight-overlay {
    display: none;
    position: static !important;
    width: 100% !important;
    max-height: 220px !important;
    overflow: auto !important;
    margin-top: 8px !important;
    padding: 10px 12px !important;
    border: 1px solid var(--warning-border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--warning-soft) !important;
    color: var(--text-body) !important;
    font-family: var(--font-mono) !important;
    font-size: 12.5px !important;
    line-height: 1.64 !important;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    pointer-events: auto;
}

.quality-preview-title {
    margin-bottom: 6px;
    color: var(--warning-text);
    font-family: var(--font-ui) !important;
    font-size: 12px;
    font-weight: 750;
}

.quality-preview-body {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}

.quality-risk-note {
    display: inline;
    color: var(--danger-text);
    font-weight: 700;
}

.quality-risk-hit {
    display: inline;
    padding: 1px 3px;
    border-radius: 4px;
    background: var(--danger-soft);
    color: var(--danger-text);
    font-weight: 800;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
}

#story_editor textarea.quality-overlay-active,
#beats_editor textarea.quality-overlay-active {
    position: relative !important;
    z-index: 2 !important;
    background: transparent !important;
    color: transparent !important;
    caret-color: var(--text-title) !important;
    text-shadow: none !important;
}

#story_editor textarea.quality-overlay-active::selection,
#beats_editor textarea.quality-overlay-active::selection {
    background: rgba(79, 70, 229, 0.22) !important;
    color: transparent !important;
}

#save_story_beats_btn,
#save_story_beats_btn button {
    max-width: 150px !important;
}

.aicf-cm-host textarea {
    display: block !important;
}

.quality-native-host {
    position: relative !important;
}

.quality-native-host textarea.native-quality-risk {
    padding-left: 34px !important;
}

.native-risk-arrow-layer {
    position: absolute;
    left: 10px;
    top: 0;
    width: 18px;
    height: 100%;
    pointer-events: none;
    overflow: hidden;
    z-index: 4;
}

.native-risk-arrow {
    position: absolute;
    left: 0;
    width: 18px;
    height: 20px;
    color: var(--danger);
    font-size: 12px;
    line-height: 20px;
    font-weight: 900;
    text-align: center;
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.72);
    pointer-events: auto;
    cursor: help;
}

.aicf-cm-host .CodeMirror {
    width: 100% !important;
    border: 1px solid var(--theme-border) !important;
    border-radius: var(--radius) !important;
    background: var(--theme-surface) !important;
    color: var(--theme-text-body) !important;
    font-family: var(--font-mono) !important;
    font-size: 12.5px !important;
    line-height: 1.64 !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.54) !important;
    overflow: hidden !important;
}

.aicf-cm-host .aicf-codemirror-story.CodeMirror {
    min-height: 260px !important;
    height: 260px !important;
}

.aicf-cm-host .aicf-codemirror-beats.CodeMirror {
    min-height: 420px !important;
    height: 420px !important;
}

.aicf-cm-host .CodeMirror-scroll {
    min-height: inherit !important;
}

.aicf-cm-host .CodeMirror-lines {
    padding: 12px 0 !important;
}

.aicf-cm-host .CodeMirror-code {
    padding: 12px !important;
    padding-left: 54px !important;
    font-family: var(--font-mono) !important;
    font-size: 12.5px !important;
    line-height: 1.64 !important;
    color: var(--theme-text-body) !important;
    caret-color: var(--theme-text-title) !important;
}

.aicf-cm-host .CodeMirror-code.has-quality-risk {
    background: transparent !important;
    box-shadow: none !important;
}

.aicf-cm-host .CodeMirror-gutters {
    border-right: 1px solid var(--theme-border) !important;
    background: var(--theme-surface-subtle) !important;
}

.aicf-cm-host .CodeMirror-linenumber {
    color: var(--theme-text-secondary) !important;
}

.aicf-cm-host .aicf-risk-gutter {
    width: 22px;
}

.aicf-cm-host .cm-risk-arrow {
    display: inline-block;
    width: 20px;
    color: var(--danger);
    font-size: 12px;
    line-height: 1;
    font-weight: 900;
    text-align: center;
    cursor: help;
}

.aicf-cm-host .CodeMirror-cursor {
    border-left-color: var(--theme-text-title) !important;
}

.aicf-cm-host .CodeMirror-focused {
    border-color: var(--theme-accent-border-strong) !important;
    box-shadow: 0 0 0 3px var(--theme-accent-soft) !important;
}

.aicf-cm-host .cm-quality-risk {
    background: transparent !important;
    color: inherit !important;
    font-weight: inherit !important;
    box-shadow: none !important;
}

/* Workflow progress */
.pipeline-status-box {
    width: 100%;
}

.status-center {
    width: min(100%, 800px);
    display: grid;
    grid-template-columns: minmax(132px, auto) minmax(240px, 1fr) minmax(164px, auto);
    gap: 12px;
    align-items: center;
    padding: 5px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--surface-subtle) 76%, var(--surface));
    box-shadow: none;
}

.pipeline-current,
.pipeline-meta {
    min-height: 30px;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1;
    white-space: nowrap;
}

.pipeline-current {
    padding-left: 0;
}

.pipeline-current strong,
.pipeline-meta strong {
    color: var(--text-card);
    font-weight: 600;
}

.pipeline-meta {
    justify-content: flex-start;
    gap: 8px;
    padding-right: 0;
}

.pipeline-progress {
    min-height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
}

.pipeline-node {
    width: 8px;
    height: 8px;
    flex: 0 0 auto;
    border: 1px solid var(--border-strong);
    border-radius: 999px;
    background: var(--border);
}

.pipeline-node.done {
    border-color: var(--success);
    background: var(--success);
}

.pipeline-node.active {
    width: 9px;
    height: 9px;
    border-color: var(--accent);
    background: var(--accent);
    box-shadow: var(--active-ring);
}

.pipeline-node.failed {
    border-color: var(--danger);
    background: var(--danger);
    box-shadow: var(--danger-ring);
}

.pipeline-arrow,
.pipeline-arrow.done,
.pipeline-arrow.active {
    color: var(--text-placeholder);
    font-size: 12px;
    line-height: 1;
}

/* Segment panel */
[hidden] {
    display: none !important;
}

.panel-heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
}

.stats-pill {
    flex: 0 0 auto;
    max-width: 48%;
    padding: 5px 9px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-subtle);
    color: var(--text-secondary);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.group-card {
    position: relative;
    margin-bottom: 10px;
    padding: 12px !important;
    transition: border-color var(--motion), background-color var(--motion), box-shadow var(--motion) !important;
}

.group-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
    gap: 10px;
    margin-bottom: 9px;
}

.segment-title-block {
    min-width: 0;
}

.group-actions {
    display: flex;
    flex-direction: column;
    flex-wrap: wrap;
    justify-content: flex-start;
    align-items: flex-end;
    gap: 6px;
    max-width: 360px;
}

.segment-status-row,
.segment-action-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: 6px;
}

.segment-action-row .card-regen-btn,
.prompt-toolbar .card-regen-btn {
    height: 36px !important;
    min-height: 36px !important;
    padding: 0 12px !important;
    font-size: 12px !important;
    line-height: 1 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    vertical-align: middle !important;
    white-space: nowrap !important;
}

.segment-action-row .primary-mini,
.prompt-toolbar .primary-mini {
    min-width: 116px;
}

.segment-action-row .action-utility,
.prompt-toolbar .action-utility {
    color: var(--text-secondary) !important;
    background: color-mix(in srgb, var(--surface-subtle) 78%, var(--surface)) !important;
    border-color: var(--border) !important;
}

/* The video-protection toggle uses a distinct semantic text color for each state. */
.segment-action-row .action-utility[aria-pressed="false"] {
    color: var(--danger-text) !important;
}

.segment-action-row .action-utility[aria-pressed="true"] {
    color: var(--warning-text) !important;
}

.segment-action-row .card-regen-btn:focus-visible,
.prompt-toolbar .card-regen-btn:focus-visible {
    outline: 2px solid var(--theme-accent, var(--theme-accent-border));
    outline-offset: 2px;
}

.segment-action-feedback {
    min-height: 16px;
    margin-top: 2px;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.35;
    text-align: right;
}

.segment-action-feedback.is-error {
    color: var(--danger-text);
}

.segment-meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
    gap: 7px;
    margin: 9px 0;
}

.segment-meta-grid.single-metric {
    grid-template-columns: minmax(118px, 132px);
}

.metric-card {
    padding: 8px 9px !important;
    background: color-mix(in srgb, var(--surface-subtle) 82%, var(--surface)) !important;
    box-shadow: none !important;
}

.metric-card span {
    display: block;
    color: var(--text-label);
    font-size: 11px;
    line-height: 1.1;
    font-weight: 600;
}

.metric-card b {
    display: block;
    margin-top: 4px;
    color: var(--text-card);
    font-size: 13px;
}

.segment-detail-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px 10px;
    margin-top: 8px;
}

.detail-item {
    min-width: 0;
    display: grid;
    grid-template-columns: 58px minmax(0, 1fr);
    align-items: baseline;
    gap: 6px;
    color: var(--text-body);
    font-size: 12.5px;
    line-height: 1.45;
}

.detail-item span {
    color: var(--text-label);
    font-size: 11.5px;
    font-weight: 600;
    white-space: nowrap;
}

.detail-item b {
    min-width: 0;
    color: var(--text-card);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.detail-wide {
    grid-column: 1 / -1;
}

.detail-wide b {
    max-width: calc(100% - 60px);
    display: inline-block;
    vertical-align: bottom;
}

.usage-summary-line,
.usage-history {
    margin-top: 10px;
    padding: 9px 11px;
    border: 1px solid color-mix(in srgb, var(--theme-accent-border) 46%, var(--border));
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--theme-accent-soft) 20%, var(--surface));
    color: var(--text-body);
    font-size: 12px;
    line-height: 1.5;
}

.usage-summary-line {
    margin: 8px 0 10px;
}

.usage-summary-line b,
.final-usage b {
    color: var(--text-card);
    font-variant-numeric: tabular-nums;
}

.usage-summary-line span {
    margin-left: 6px;
    color: var(--text-secondary);
}

.usage-history summary {
    cursor: pointer;
    color: var(--text-card);
    font-weight: 650;
}

.usage-table-wrap {
    overflow-x: auto;
    margin-top: 8px;
}

.usage-history table {
    width: 100%;
    min-width: 760px;
    border-collapse: collapse;
    font-variant-numeric: tabular-nums;
}

.usage-history th,
.usage-history td {
    padding: 6px 8px;
    border-top: 1px solid var(--border);
    text-align: left;
    white-space: nowrap;
}

.usage-history th {
    color: var(--text-label);
    font-size: 11px;
    font-weight: 650;
}

.usage-history td {
    color: var(--text-body);
}

.usage-task-id {
    max-width: 190px;
    overflow: hidden;
    text-overflow: ellipsis;
}

.usage-model-label {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
}

.usage-history-row-playable {
    cursor: pointer;
}

.usage-history-row-playable:hover td {
    background: color-mix(in srgb, var(--theme-accent-soft) 46%, transparent);
}

.usage-row-actions {
    display: inline-flex;
    align-items: center;
    gap: 5px;
}

.usage-icon-btn {
    display: inline-grid;
    width: 30px;
    height: 28px;
    place-items: center;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface);
    color: var(--text-body);
    font: inherit;
    line-height: 1;
    cursor: pointer;
}

.usage-icon-btn svg {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.usage-prompt-btn:hover:not(:disabled) {
    border-color: var(--theme-accent-border-strong);
    color: var(--theme-accent);
}

.usage-select-btn:hover:not(:disabled),
.usage-select-btn.is-active {
    border-color: var(--theme-accent-border-strong);
    background: var(--theme-accent-soft);
    color: var(--theme-accent);
}

.usage-select-btn.is-active {
    box-shadow: inset 0 0 0 1px var(--theme-accent-border-strong);
}

.usage-select-btn.is-loading svg {
    opacity: .35;
}

.usage-delete-cell {
    width: 108px;
    text-align: center !important;
}

.usage-delete-btn {
    color: var(--danger-text);
    font-size: 17px;
    font-weight: 650;
}

.usage-delete-btn:hover:not(:disabled) {
    border-color: var(--danger);
    background: color-mix(in srgb, var(--danger) 10%, var(--surface));
}

.usage-icon-btn:focus-visible {
    outline: 2px solid var(--theme-accent-border-strong);
    outline-offset: 2px;
}

.usage-icon-btn:disabled {
    cursor: not-allowed;
    opacity: .42;
}

.usage-muted {
    color: var(--text-secondary);
    font-size: 11px;
}

.usage-attempt-prompt-overlay {
    position: fixed;
    inset: 0;
    z-index: 10000;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background: var(--preview-scrim);
    backdrop-filter: blur(6px);
}

.usage-attempt-prompt-overlay.open {
    display: flex;
}

.usage-attempt-prompt-dialog {
    width: min(940px, 96vw);
    max-height: min(760px, 88vh);
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    box-shadow: 0 24px 80px rgba(15, 23, 42, .28);
}

.usage-attempt-prompt-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    color: var(--text-card);
}

.usage-attempt-prompt-close {
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 18px;
}

.usage-attempt-prompt-close:hover,
.usage-attempt-prompt-close:focus-visible {
    background: var(--surface-subtle);
    color: var(--text-card);
}

.usage-attempt-prompt-body {
    max-height: calc(min(760px, 88vh) - 57px);
    overflow: auto;
    margin: 0;
    padding: 14px;
    color: var(--text-body);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
    line-height: 1.6;
}

.final-usage {
    margin-left: 10px;
    color: var(--text-secondary);
    font-size: 12px;
}

.stale-video-line {
    display: block;
    margin-top: 4px;
    color: var(--text-secondary);
    font-size: 11.5px;
}

.stale-video-line b {
    max-width: min(100%, 320px);
    color: var(--text-secondary);
    font-weight: 600;
}

.stale-video-line .card-regen-btn {
    margin-left: 6px;
}

.prompt-block {
    margin-top: 10px;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    background: color-mix(in srgb, var(--surface-subtle) 82%, var(--surface)) !important;
    box-shadow: none !important;
    overflow: hidden;
}

.prompt-summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 9px 11px;
    color: var(--text-card);
    cursor: pointer;
    font-size: 12.5px;
    font-weight: 650;
}

.prompt-summary small {
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 500;
}

.prompt-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
    padding: 0 11px 9px;
}

.prompt-toolbar .card-regen-btn {
    margin: 0 !important;
}

.prompt-preview {
    margin: 0;
    padding: 11px 12px !important;
    background: transparent !important;
    box-shadow: none !important;
    font-size: 12px;
    line-height: 1.6;
}

.prompt-full {
    display: block;
    width: 100%;
    max-height: none;
    overflow: visible;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-wrap: anywhere;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.segment-editor-block {
    border-color: color-mix(in srgb, var(--theme-accent-border) 56%, var(--border)) !important;
    background: linear-gradient(180deg, color-mix(in srgb, var(--theme-accent-soft) 38%, var(--surface)) 0%, var(--surface) 100%) !important;
}

.segment-editor-toolbar {
    align-items: center;
    border-bottom: 1px solid var(--border);
}

.segment-director-editor {
    width: calc(100% - 22px);
    min-height: 300px !important;
    height: 300px !important;
    margin: 0 11px 11px;
    padding: 11px 12px;
    resize: vertical;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    color: var(--text-body);
    outline: none;
    box-shadow: inset 0 1px 0 color-mix(in srgb, var(--surface-subtle) 78%, transparent);
    font-size: 12.5px;
    line-height: 1.62;
}

textarea.segment-director-editor.prompt-full {
    min-height: 300px !important;
    height: 300px !important;
    overflow: auto !important;
}

.segment-director-editor:focus {
    border-color: var(--theme-accent-border-strong);
    box-shadow: 0 0 0 3px var(--theme-accent-soft), inset 0 1px 0 color-mix(in srgb, var(--surface-subtle) 78%, transparent);
}

.segment-source-excerpt {
    width: calc(100% - 22px);
    min-height: 120px;
    height: 120px;
    margin: 0 11px 11px;
    padding: 11px 12px;
    resize: vertical;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-subtle) 55%, var(--surface));
    color: var(--text-body);
    outline: none;
    font-size: 12.5px;
    line-height: 1.62;
}

textarea.segment-source-excerpt.prompt-full {
    min-height: 120px !important;
    height: 120px !important;
    overflow: auto !important;
}

.primary-mini {
    border-color: var(--theme-accent-border-strong) !important;
    background: var(--theme-accent) !important;
    color: var(--theme-accent-foreground) !important;
    text-shadow: none !important;
}

.primary-mini:hover {
    background: var(--theme-accent-hover) !important;
}

.aicf-prompt-modal {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background: rgba(15, 23, 42, 0.58);
    backdrop-filter: blur(8px);
}

.aicf-prompt-modal.open {
    display: flex;
}

.aicf-prompt-modal-card {
    display: flex;
    flex-direction: column;
    width: min(1280px, 96vw);
    height: 92vh;
    max-height: 92vh;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    box-shadow: 0 24px 70px rgba(15, 23, 42, 0.28);
    overflow: hidden;
}

.aicf-prompt-modal-head {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    color: var(--text-card);
}

.aicf-prompt-modal-context {
    display: grid;
    min-width: 0;
    gap: 3px;
}

.aicf-prompt-modal-segment {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 8px;
    color: var(--text-muted);
    font-size: 12px;
    line-height: 1.35;
}

.aicf-prompt-modal-part {
    flex: 0 0 auto;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-subtle);
    color: var(--text-label);
    font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
    font-size: 11px;
    font-weight: 700;
}

.aicf-prompt-modal-segment-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.aicf-prompt-modal-actions {
    display: flex;
    align-items: center;
    gap: 8px;
}

.aicf-prompt-modal-head button {
    min-width: 44px;
    min-height: 44px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface-subtle);
    color: var(--text-card);
    cursor: pointer;
}

.aicf-prompt-modal-head .aicf-prompt-modal-force-save {
    padding: 0 14px;
}

.aicf-prompt-modal-head .aicf-prompt-modal-save {
    padding: 0 14px;
    border-color: var(--theme-accent-border-strong);
    background: var(--theme-accent);
    color: var(--theme-accent-foreground);
    font-weight: 700;
}

.aicf-prompt-modal-head button:focus-visible,
.aicf-material-option:focus-visible {
    outline: 2px solid var(--theme-accent);
    outline-offset: 2px;
}

.aicf-prompt-modal-content {
    display: grid;
    grid-template-columns: minmax(320px, 360px) minmax(0, 1fr);
    flex: 1 1 auto;
    min-height: 0;
}

.aicf-prompt-material-panel {
    min-width: 0;
    padding: 16px;
    border-right: 1px solid var(--border);
    background: var(--surface-subtle);
    overflow-y: auto;
}

.aicf-material-panel-title {
    margin-bottom: 16px;
    color: var(--text-card);
    font-size: 15px;
    font-weight: 750;
}

.aicf-material-section + .aicf-material-section {
    margin-top: 20px;
}

.aicf-material-section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
    color: var(--text-card);
    font-size: 13px;
}

.aicf-material-section-head span {
    color: var(--text-muted);
    font-size: 12px;
    font-weight: 650;
}

.aicf-material-grid {
    display: grid;
    gap: 8px;
}

.aicf-material-identity {
    display: grid;
    gap: 8px;
    padding: 12px 0;
    border-top: 1px solid var(--border);
}

.aicf-material-identity:first-child {
    padding-top: 0;
    border-top: 0;
}

.aicf-material-identity-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
}

.aicf-material-identity-head > div {
    display: grid;
    min-width: 0;
    gap: 2px;
}

.aicf-material-identity-head strong,
.aicf-material-identity-head small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.aicf-material-identity-head strong {
    color: var(--text-card);
    font-size: 13px;
}

.aicf-material-identity-head small {
    color: var(--text-muted);
    font-size: 11px;
}

.aicf-material-identity-state {
    flex: 0 0 auto;
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 650;
}

.aicf-material-identity.is-role-selected .aicf-material-identity-state {
    color: var(--theme-accent);
}

.aicf-character-images {
    display: grid;
    gap: 8px;
}

.aicf-material-option {
    position: relative;
    display: grid;
    grid-template-columns: 76px minmax(0, 1fr);
    align-items: center;
    gap: 10px;
    width: 100%;
    min-height: 86px;
    padding: 6px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text-card);
    text-align: left;
    cursor: pointer;
    transition: opacity 180ms ease, border-color 180ms ease, background-color 180ms ease;
}

.aicf-material-option.is-muted {
    opacity: 0.52;
}

.aicf-material-option.is-muted .aicf-material-option-media img {
    filter: grayscale(0.72);
}

.aicf-material-option.is-muted:hover {
    opacity: 0.82;
    border-color: var(--theme-accent-border-strong);
}

.aicf-material-option.is-selected {
    opacity: 1;
    border-color: var(--theme-accent-border-strong);
    background: var(--theme-accent-soft);
    box-shadow: inset 3px 0 0 var(--theme-accent);
}

.aicf-material-option:disabled {
    opacity: 0.38;
    cursor: not-allowed;
}

.aicf-material-option-media {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 76px;
    height: 72px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface-muted);
    overflow: hidden;
}

.aicf-material-option-media img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: filter 180ms ease;
}

.aicf-material-option-text {
    display: grid;
    min-width: 0;
    gap: 3px;
    padding-right: 6px;
}

.aicf-material-option-text strong,
.aicf-material-option-text small {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.aicf-material-option-text strong {
    font-size: 13px;
}

.aicf-material-option-text small {
    color: var(--text-muted);
    font-size: 11px;
}

.aicf-material-option-state {
    position: absolute;
    right: 7px;
    bottom: 7px;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-subtle);
    color: var(--text-muted);
    font-size: 10px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}

.aicf-material-option.is-selected .aicf-material-option-state {
    border-color: var(--theme-accent-border-strong);
    background: var(--theme-accent);
    color: var(--theme-accent-foreground);
}

.aicf-material-missing,
.aicf-material-empty {
    color: var(--text-muted);
    font-size: 12px;
}

.aicf-material-empty {
    padding: 14px 10px;
    border: 1px dashed var(--border);
    border-radius: 6px;
    text-align: center;
}

.aicf-material-feedback {
    min-height: 20px;
    margin-top: 12px;
    color: var(--text-muted);
    font-size: 12px;
    line-height: 1.5;
}

.aicf-material-feedback.is-error {
    color: var(--danger-text);
}

.aicf-prompt-editor-pane {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    background: var(--surface);
}

.aicf-prompt-editor-head {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 10px 16px 8px;
}

.aicf-prompt-editor-head > label {
    color: var(--text-label);
    font-size: 12px;
    font-weight: 700;
}

.aicf-prompt-editor-head > small {
    color: var(--text-muted);
    font-size: 11px;
    line-height: 1.4;
    text-align: right;
}

.aicf-prompt-modal-feedback {
    min-height: 18px;
    color: var(--text-muted);
    font-size: 11px;
    line-height: 1.4;
    text-align: right;
}

.aicf-prompt-modal-feedback.is-error {
    color: var(--danger-text);
}

.panel-subtitle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}

.panel-subtitle-row .panel-subtitle {
    min-width: 0;
}

.segment-ok-filter {
    flex: 0 0 auto;
    min-width: 32px;
    min-height: 24px !important;
    padding: 0 7px !important;
    border: 1px solid var(--success-border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--success-soft) !important;
    color: var(--success-text) !important;
    font-size: 11px !important;
    font-weight: 800 !important;
    line-height: 1 !important;
}

.segment-ok-filter.is-filtering {
    border-color: var(--border) !important;
    background: var(--surface-muted) !important;
    color: var(--text-secondary) !important;
}

.group-card.is-video-ok-filtered {
    display: none;
}

.aicf-prompt-modal.is-refreshing .aicf-prompt-modal-body {
    opacity: 0.72;
    cursor: progress;
}

.aicf-prompt-modal-body {
    display: block;
    flex: 1 1 auto;
    width: 100%;
    min-height: 0;
    height: auto;
    margin: 0;
    padding: 16px;
    resize: none;
    border: 0;
    border-top: 1px solid var(--border);
    outline: none;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-wrap: anywhere;
    background: var(--surface);
    color: var(--text-body);
    font-size: 13px;
    line-height: 1.65;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

@media (max-width: 860px) {
    .aicf-prompt-modal {
        padding: 8px;
    }

    .aicf-prompt-modal-card {
        width: 100%;
        height: 96vh;
        max-height: 96vh;
    }

    .aicf-prompt-modal-content {
        display: flex;
        flex-direction: column;
        overflow-y: auto;
    }

    .aicf-prompt-material-panel {
        flex: 0 0 auto;
        max-height: 46vh;
        border-right: 0;
        border-bottom: 1px solid var(--border);
    }

    .aicf-prompt-editor-pane {
        flex: 0 0 auto;
        min-height: 46vh;
    }

    .aicf-prompt-editor-head {
        align-items: flex-start;
        flex-direction: column;
        gap: 4px;
    }

    .aicf-prompt-editor-head > small {
        text-align: left;
    }
}

@media (prefers-reduced-motion: reduce) {
    .aicf-material-option,
    .aicf-material-option-media img,
    .aicf-prompt-modal-body {
        transition: none;
    }
}

.final-output-line {
    margin-top: 12px;
    padding: 10px 11px !important;
    border-color: var(--success-border) !important;
    background: var(--success-soft) !important;
    color: var(--success-text) !important;
    box-shadow: none !important;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    min-height: 22px;
    padding: 2px 8px;
    border: 1px solid var(--success-border);
    border-radius: 999px;
    background: var(--success-soft);
    color: var(--success-text);
    font-size: 12px;
    font-weight: 650;
}

.status-pending {
    border-color: var(--warning-border);
    background: var(--warning-soft);
    color: var(--warning-text);
}

.status-failed {
    border-color: var(--danger-border);
    background: var(--danger-soft);
    color: var(--danger-text);
}

.path-line b {
    word-break: break-all;
}

.dev-drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
}

.dev-drawer-title {
    color: var(--text-card);
    font-size: 15px;
    font-weight: 700;
}

.dev-tabs {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
    padding: 3px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-subtle);
}

.dev-tab-btn {
    flex: 1;
    min-height: 32px !important;
    border-color: transparent !important;
    background: transparent !important;
}

.dev-tab-btn.active {
    border-color: var(--border) !important;
    background: var(--surface) !important;
    color: var(--accent) !important;
    box-shadow: var(--shadow) !important;
}

.theme-switcher {
    margin: 0 0 14px;
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent-soft) 30%, var(--surface)) 0%, var(--surface) 100%);
    box-shadow: var(--shadow);
}

.theme-switcher-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
    color: var(--text-card);
    font-size: 13px;
    font-weight: 650;
}

.theme-switcher-caption {
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 400;
}

.theme-switcher-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(118px, 1fr));
    gap: 8px;
}

.theme-choice-input {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

.kv-settings-form {
    gap: 10px !important;
    margin-bottom: 12px !important;
}

.kv-row {
    display: grid !important;
    grid-template-columns: 190px minmax(0, 1fr) !important;
    gap: 12px !important;
    align-items: center !important;
}

.kv-key {
    min-height: 38px;
    display: flex;
    align-items: center;
    padding: 0 4px;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--text-card);
    font-size: 13px;
    font-weight: 700;
}

.kv-row .wrap,
.kv-row .form,
.kv-row .block {
    min-width: 0 !important;
}

.kv-row .label-wrap,
.kv-row .block-label,
.kv-row .label,
.kv-row .block-info,
.kv-row .form > label,
.kv-row .wrap > label,
.kv-row > div:nth-child(2) > label,
.kv-row > div:nth-child(2) .label-wrap,
.kv-row > div:nth-child(2) .label-wrap *,
.kv-row > div:nth-child(2) .block-label,
.kv-row > div:nth-child(2) .block-label *,
.kv-row > div:nth-child(2) .block-info,
.kv-row > div:nth-child(2) .block-info * {
    display: none !important;
}

.kv-row input,
.kv-row textarea,
.kv-row select {
    min-height: 38px !important;
}

.inline-save-status {
    min-height: 20px;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.5;
}

.theme-choice {
    position: relative !important;
    display: inline-flex !important;
    align-items: center !important;
    min-height: 42px !important;
    justify-content: flex-start !important;
    padding: 0 12px 0 34px !important;
    border-radius: 8px !important;
    background: var(--surface-subtle) !important;
    font-size: 12px !important;
    overflow: hidden !important;
    transition: background-color var(--motion), border-color var(--motion), color var(--motion), box-shadow var(--motion), transform var(--motion) !important;
}

.theme-choice::before {
    content: "";
    position: absolute;
    left: 12px;
    width: 12px;
    height: 12px;
    border-radius: 999px;
    border: 1px solid rgba(15, 23, 42, 0.12);
    background: var(--accent);
}

.theme-choice[data-theme="linear"]::before {
    background: #2563eb;
}

.theme-choice[data-theme="cursor"]::before {
    background: #8b5cf6;
}

.theme-choice[data-theme="vercel"]::before {
    background: #000000;
}

.theme-choice[data-theme="glass"]::before {
    background: #6366f1;
}

.theme-choice[data-theme="ocean"]::before {
    background: #0284c7;
}

.theme-choice[data-theme="purple"]::before {
    background: #7c3aed;
}

.theme-choice[data-theme="cyber"]::before {
    background: #22d3ee;
}

.theme-choice[data-theme="classic"]::before {
    background: #2563eb;
}

.theme-choice.active,
.theme-choice[aria-pressed="true"],
.theme-choice-input:checked + .theme-choice,
body[data-aicf-theme="linear"] .theme-choice[data-theme="linear"],
body[data-aicf-theme="cursor"] .theme-choice[data-theme="cursor"],
body[data-aicf-theme="vercel"] .theme-choice[data-theme="vercel"],
body[data-aicf-theme="glass"] .theme-choice[data-theme="glass"],
body[data-aicf-theme="ocean"] .theme-choice[data-theme="ocean"],
body[data-aicf-theme="purple"] .theme-choice[data-theme="purple"],
body[data-aicf-theme="cyber"] .theme-choice[data-theme="cyber"],
body[data-aicf-theme="classic"] .theme-choice[data-theme="classic"] {
    min-height: 48px !important;
    border-color: var(--accent-border-strong) !important;
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent-soft) 78%, var(--surface)) 0%, var(--surface) 100%) !important;
    color: var(--accent) !important;
    box-shadow: var(--active-ring), var(--shadow-hover) !important;
    transform: translateY(-1px) !important;
}

.theme-choice.active::after,
.theme-choice[aria-pressed="true"]::after,
.theme-choice-input:checked + .theme-choice::after,
body[data-aicf-theme="linear"] .theme-choice[data-theme="linear"]::after,
body[data-aicf-theme="cursor"] .theme-choice[data-theme="cursor"]::after,
body[data-aicf-theme="vercel"] .theme-choice[data-theme="vercel"]::after,
body[data-aicf-theme="glass"] .theme-choice[data-theme="glass"]::after,
body[data-aicf-theme="ocean"] .theme-choice[data-theme="ocean"]::after,
body[data-aicf-theme="purple"] .theme-choice[data-theme="purple"]::after,
body[data-aicf-theme="cyber"] .theme-choice[data-theme="cyber"]::after,
body[data-aicf-theme="classic"] .theme-choice[data-theme="classic"]::after {
    content: "ACTIVE";
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--accent);
    font-size: 11px;
    font-weight: 750;
}

body[data-aicf-theme="glass"] .app-header,
body[data-aicf-theme="glass"] .workspace-card,
body[data-aicf-theme="glass"] .side-card,
body[data-aicf-theme="glass"] .right-panel,
body[data-aicf-theme="glass"] .output-card,
body[data-aicf-theme="glass"] .group-card,
body[data-aicf-theme="glass"] .key-img-preview-container {
    backdrop-filter: blur(18px) saturate(160%);
}

/* Preview modal */
.key-img-preview-overlay {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 10000;
    align-items: center;
    justify-content: center;
    background: var(--preview-scrim);
}

.key-img-preview-overlay.open {
    display: flex;
}

.key-img-preview-container {
    position: relative;
    max-width: 90vw;
    max-height: 90vh;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    box-shadow: var(--shadow-overlay);
}

.key-img-preview-close {
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 1;
    width: 34px;
    min-width: 34px !important;
    height: 34px;
    min-height: 34px !important;
    padding: 0 !important;
}

.key-img-preview-img {
    display: block;
    max-width: 85vw;
    max-height: 80vh;
    object-fit: contain;
    border-radius: var(--radius-sm);
}

video.key-img-preview-img {
    width: 85vw;
    height: auto;
    max-height: 80vh;
}

.key-img-preview-info {
    margin-top: 8px;
    text-align: center;
}

/* Hidden/auxiliary states */
body:not(.minimal-mode) .one-click-action-row {
    display: none !important;
}

body.minimal-mode .main-action-row {
    display: none !important;
}

body.minimal-mode .one-click-action-row {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
}

body.minimal-mode .one-click-main {
    width: 100% !important;
    max-width: 360px;
}

#status_log,
.scroll-log {
    display: none !important;
}

div[role="button"] {
    cursor: pointer !important;
}

/* Responsive */
@media (max-width: 1280px) {
    .asset-library-grid {
        grid-template-columns: 1fr !important;
    }

    .asset-panel {
        min-height: auto !important;
    }

    .app-shell {
        flex-wrap: wrap !important;
        gap: 12px !important;
    }

    .app-shell > .sidebar-column,
    .app-shell > .workspace-column,
    .app-shell > .output-column {
        flex: 1 1 100% !important;
        max-width: none !important;
        min-width: min(720px, 100%) !important;
    }

    .output-column {
        position: static;
        max-height: none;
        overflow: visible;
    }

    .main-action-row {
        grid-template-columns: repeat(3, minmax(132px, 1fr)) !important;
    }

    .theme-switcher-grid {
        grid-template-columns: repeat(2, minmax(118px, 1fr));
    }
}

@media (max-width: 920px) {
    .asset-library-header {
        grid-template-columns: 1fr !important;
    }

    .asset-library-header .primary-btn,
    .asset-library-header .primary-btn button {
        width: 100% !important;
    }

    .gradio-container {
        padding: 12px !important;
    }

    .app-header {
        grid-template-columns: 1fr auto !important;
    }

    .app-header > *:nth-child(2) {
        grid-column: 1 / -1 !important;
        grid-row: 2 !important;
    }

    .status-center {
        width: 100%;
        grid-template-columns: 1fr;
        gap: 6px;
    }

    .pipeline-current,
    .pipeline-meta {
        justify-content: flex-start;
        padding: 0 8px;
        flex-wrap: wrap;
    }

    .pipeline-progress {
        justify-content: flex-start;
        overflow-x: auto;
        scrollbar-width: none;
    }

    .pipeline-progress::-webkit-scrollbar {
        display: none;
    }

    .section-title {
        font-size: 18px !important;
    }

    .main-action-row {
        grid-template-columns: repeat(2, minmax(132px, 1fr)) !important;
    }

    .theme-switcher-grid {
        grid-template-columns: 1fr;
    }

    .group-head {
        grid-template-columns: 1fr;
    }

    .group-actions {
        justify-content: flex-start;
        align-items: flex-start;
        max-width: none;
        width: 100%;
    }

    .segment-status-row,
    .segment-action-row {
        justify-content: flex-start;
    }

    .panel-heading {
        flex-direction: column;
    }

    .stats-pill {
        max-width: 100%;
        white-space: normal;
        border-radius: var(--radius-sm);
    }

    .segment-meta-grid,
    .segment-detail-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}

@media (max-width: 560px) {
    .gradio-container {
        padding: 8px !important;
    }

    .app-header {
        grid-template-columns: 1fr !important;
    }

    .top-actions {
        justify-content: flex-start;
    }

    .workspace-card,
    .side-card,
    .right-panel,
    .output-card {
        padding: 12px !important;
    }

    .main-action-row {
        grid-template-columns: 1fr !important;
    }

    .segment-meta-grid,
    .segment-detail-grid {
        grid-template-columns: 1fr;
    }

    .segment-action-row .card-regen-btn,
    .prompt-toolbar .card-regen-btn {
        flex: 1 1 132px;
    }

    .detail-wide b {
        max-width: 100%;
    }

    #story_editor textarea,
    #beats_editor textarea {
        min-height: 260px !important;
    }
}

.beat-risk-notice {
    display: grid;
    gap: 4px;
    margin: 0 0 10px;
    padding: 10px 12px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    line-height: 1.5;
}

.beat-risk-notice b {
    font-size: 14px;
}

.beat-risk-notice span {
    display: block;
}

.beat-risk-list {
    display: grid;
    gap: 6px;
    margin: 4px 0;
    padding: 0;
    list-style: none;
}

.beat-risk-item {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 6px 8px;
    border: 1px solid var(--warning-border);
    border-radius: 6px;
    background: color-mix(in srgb, var(--warning-soft) 72%, white);
}

.beat-risk-select {
    min-height: 32px;
    padding: 4px 8px;
    border: 1px solid #d97706;
    border-radius: 5px;
    background: #fff;
    color: #92400e;
    font-weight: 700;
    cursor: pointer;
}

.beat-risk-select:hover,
.beat-risk-select:focus-visible {
    outline: none;
    border-color: #92400e;
    background: #fef3c7;
}

.beat-risk-title {
    font-weight: 600;
}

.beat-risk-codes {
    color: #a16207 !important;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 12px;
}

.beat-risk-notice.warn {
    border: 1px solid var(--warning-border);
    background: var(--warning-soft);
    color: #92400e !important;
}

.beat-risk-notice.ok {
    border: 1px solid var(--success-border);
    background: var(--success-soft);
    color: #166534 !important;
}

/* Gradio may apply disabled-label colors inside this accordion. Keep result
   messages and the opt-in checkbox readable regardless of the active theme. */
.beat-risk-notice,
.beat-risk-notice b,
.beat-risk-notice span {
    opacity: 1 !important;
}

.beat-risk-notice.warn b,
.beat-risk-notice.warn span {
    color: #92400e !important;
}

.beat-risk-notice.ok b,
.beat-risk-notice.ok span {
    color: #166534 !important;
}

#complex_beat_index label,
#complex_beat_index .label-wrap,
#complex_beat_index .label-wrap span,
#ignore_beat_risk_box label,
#ignore_beat_risk_box .label-wrap,
#ignore_beat_risk_box .label-wrap span,
#ignore_beat_risk_box span {
    color: var(--text-body) !important;
    opacity: 1 !important;
}

.aicf-hidden-trigger {
    position: absolute !important;
    left: -9999px !important;
    top: auto !important;
    width: 1px !important;
    height: 1px !important;
    overflow: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

/* Gradio wraps hidden Textboxes in an in-flow form. Although the control is
   absolutely positioned, each empty wrapper still consumes the root 16px
   flex gap. Remove those wrappers so they do not create a large blank band. */
.gradio-container .form:has(.aicf-hidden-trigger) {
    display: none !important;
}

/* Once auxiliary wrappers are removed, keep page navigation close to the
   account cards while preserving an intentional 8px section separation. */
#official_platform_section ~ .form:has(#main_page_tabs) {
    margin-top: -8px !important;
}

#main_page_tabs {
    margin-top: 0 !important;
    padding-top: 0 !important;
}

.aicf-force-hidden {
    display: none !important;
}

.main-action-row.aicf-force-grid {
    display: grid !important;
}

.one-click-action-row.aicf-force-flex {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
}

body:not(.minimal-mode) .one-click-action-row,
body:not(.minimal-mode) .one-click-action-row.aicf-force-hidden {
    display: none !important;
}

body.minimal-mode .main-action-row,
body.minimal-mode .main-action-row.aicf-force-hidden {
    display: none !important;
}

body.minimal-mode .one-click-action-row {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
}

/* Model-factory controls stay compact and only appear inside their segment. */
.segment-model-row {
    margin: 10px 11px 0;
}

.segment-model-field {
    display: grid;
    grid-template-columns: 100px minmax(180px, 330px);
    align-items: center;
    gap: 8px 10px;
    color: var(--text-body);
    font-size: 12px;
}

.segment-model-field > span:first-child,
.h3-prompt-field > span {
    color: var(--text-muted);
    font-weight: 650;
}

.segment-model-field small {
    grid-column: 2;
    color: var(--text-muted);
    line-height: 1.45;
}

.segment-model-select {
    min-height: 36px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface);
    color: var(--text-body);
    padding: 6px 9px;
}

.segment-model-select:focus-visible,
.h3-prompt-override:focus-visible,
.h3-asset-input:focus-visible,
.h3-asset-role:focus-visible {
    outline: 3px solid var(--theme-accent-soft);
    outline-offset: 1px;
    border-color: var(--theme-accent-border-strong);
}

.segment-model-error {
    grid-column: 2;
    min-height: 16px;
    color: var(--danger, #b42318);
    line-height: 1.35;
}

.segment-model-error.is-success {
    color: var(--success, #18794e);
}

.h3-config-block {
    margin: 11px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-subtle);
}

.h3-config-block > summary {
    cursor: pointer;
    padding: 10px 12px;
    color: var(--text-body);
    font-weight: 680;
}

.h3-config-hint {
    margin: 0 12px 10px;
    color: var(--text-muted);
    font-size: 12px;
    line-height: 1.5;
}

.h3-asset-list ul {
    display: grid;
    gap: 6px;
    list-style: none;
    margin: 0 12px 10px;
    padding: 0;
}

.h3-asset-list li {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 32px;
    padding: 5px 7px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    font-size: 12px;
}

.h3-asset-list li > span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.h3-asset-role {
    min-width: 120px;
    max-width: 190px;
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 4px 6px;
    background: var(--surface);
    color: var(--text-body);
    font: inherit;
    font-size: 11px;
}

.h3-asset-list li small {
    margin-left: auto;
    color: var(--text-muted);
}

.h3-video-audio-toggle {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin-left: auto;
    color: var(--text-muted);
    font-size: 11px;
    white-space: nowrap;
}

.h3-video-audio-toggle input {
    accent-color: var(--theme-accent, #2563eb);
}

.h3-asset-list .card-regen-btn {
    margin-left: auto;
}

.h3-asset-list .muted {
    color: var(--text-muted);
}

.h3-upload-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin: 0 12px 10px;
}

.h3-upload-grid label,
.h3-prompt-field {
    display: grid;
    gap: 5px;
    color: var(--text-body);
    font-size: 12px;
}

.h3-asset-input {
    min-width: 0;
    border: 1px dashed var(--border);
    border-radius: 6px;
    padding: 6px;
    color: var(--text-muted);
    background: var(--surface);
}

.h3-prompt-field {
    margin: 0 12px 10px;
}

.h3-prompt-override {
    min-height: 120px;
    resize: vertical;
    border: 1px solid var(--border);
    border-radius: 7px;
    padding: 8px;
    background: var(--surface);
    color: var(--text-body);
    font: inherit;
    line-height: 1.55;
}

.h3-config-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    padding: 0 12px 12px;
}

.h3-config-status {
    color: var(--text-muted);
    font-size: 12px;
}

.h3-config-status.is-error {
    color: var(--danger, #b42318);
}

.h3-prompt-block > .h3-config-status {
    display: block;
    min-height: 16px;
    margin: 0 12px 10px;
}

.aicf-prompt-material-panel[hidden] {
    display: none !important;
}

.aicf-h3-config-panel .h3-config-hint {
    margin: 0 0 14px;
}

.h3-project-image-picker {
    display: grid;
    gap: 10px;
    margin: 0 0 16px;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-subtle);
}

.h3-project-image-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
}

.h3-project-image-head span,
.h3-project-image-picker > p {
    margin: 0;
    color: var(--text-muted);
    font-size: 11px;
    line-height: 1.45;
}

.h3-project-image-group {
    display: grid;
    gap: 7px;
}

.h3-project-image-group > strong {
    color: var(--text-body);
    font-size: 12px;
}

.h3-project-image-option {
    min-width: 0;
}

.h3-selected-preview {
    margin: 0 0 16px;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-subtle);
}

.h3-selected-preview .aicf-material-panel-title {
    margin-bottom: 8px;
    font-size: 13px;
}

.h3-preview-actions { display: flex; flex-direction: column; gap: 6px; }
.h3-replace-box { padding: 10px; margin: 8px 0; border: 1px solid #6366f1; border-radius: 8px; background: #f5f7ff; color: #172033; }
.h3-replace-box p { margin: 0 0 8px; line-height: 1.5; }
.h3-replace-box input { width: 100%; margin: 8px 0; }
.h3-replace-box button { min-height: 36px; margin: 4px 6px 0 0; }
.h3-preview-actions button:focus-visible, .h3-replace-box button:focus-visible { outline: 2px solid #4f46e5; outline-offset: 2px; }
.h3-selected-preview-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 6px;
}

.h3-selected-preview-list li {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    background: var(--surface-card);
    border-radius: 6px;
    border: 1px solid var(--border);
}

.h3-preview-num {
    flex-shrink: 0;
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--accent-primary, #4f46e5);
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    border-radius: 50%;
}

.h3-preview-order {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    gap: 3px;
}

.h3-preview-move-group {
    display: inline-flex;
    flex-direction: column;
    gap: 2px;
}

.h3-preview-move {
    width: 22px;
    height: 18px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface-card);
    color: var(--text-card);
    cursor: pointer;
    transition: background-color 160ms ease-out, border-color 160ms ease-out, color 160ms ease-out;
}

.h3-preview-move svg {
    width: 13px;
    height: 13px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.h3-preview-move:hover:not(:disabled) {
    border-color: var(--accent-primary, #4f46e5);
    background: var(--accent-soft);
    color: var(--accent-primary, #4f46e5);
}

.h3-preview-move:focus-visible {
    outline: 2px solid var(--focus, #6366f1);
    outline-offset: 1px;
}

.h3-preview-move:disabled {
    opacity: 0.38;
    cursor: not-allowed;
}

.h3-preview-thumb {
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    object-fit: cover;
    border-radius: 4px;
    border: 1px solid var(--border);
}

.h3-preview-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
}

.h3-preview-info strong {
    font-size: 12px;
    color: var(--text-card);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.h3-preview-info small {
    font-size: 10px;
    color: var(--text-muted);
}

.h3-preview-remove {
    flex-shrink: 0;
    font-size: 11px;
    padding: 2px 8px;
}

.aicf-h3-config-panel .h3-asset-list ul {
    margin: 0 0 16px;
}

.aicf-h3-config-panel .h3-upload-grid {
    grid-template-columns: 1fr;
    margin: 0 0 16px;
}

.aicf-h3-config-panel .h3-config-actions {
    padding: 0;
}

.aicf-h3-config-panel .h3-config-actions .card-regen-btn {
    min-height: 40px;
}

@media (max-width: 760px) {
    .segment-model-field {
        grid-template-columns: 1fr;
    }

    .segment-model-field small,
    .segment-model-error {
        grid-column: 1;
    }

    .h3-upload-grid {
        grid-template-columns: 1fr;
    }

    .h3-asset-list li {
        flex-wrap: wrap;
    }

    .h3-asset-role {
        flex: 1 1 150px;
        max-width: none;
    }
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
    }
}
"""
