# AI Cartoon Factory Design System

## Product Direction

AI Cartoon Factory should feel like a premium AI workspace: calm, compact, developer-first, and suitable for all-day use. The reference quality bar is Cursor IDE, Linear, Vercel Dashboard, Claude, Raycast, shadcn/ui, and OpenAI Platform.

This system is presentation-only. It must not change business logic, APIs, data structures, state management, event handlers, routing, component communication, or existing workflows.

## Typography

- Font family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, Microsoft YaHei, sans-serif.
- Mono/editor font: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Cascadia Mono, Microsoft YaHei UI, monospace.
- Page title: 32px, 700, line-height 1.2.
- Section title: 20px, 600, line-height 1.3.
- Card title: 16px, 500-600, line-height 1.35.
- Body: 14px, 400, line-height 1.5.
- Compact UI text: 13px, 400-500, line-height 1.4.
- Caption: 12px, 400, line-height 1.35.
- Letter spacing: 0 by default. Use 0.08em only for small uppercase kickers.
- Hierarchy should come from size, weight, spacing, and alignment before color.

## Color Tokens

- Background: `#F8FAFC`
- Background subtle: `#F6F7F9`
- Surface: `#FFFFFF`
- Surface subtle: `#F9FAFB`
- Border: `#E5E7EB`
- Border strong: `#D1D5DB`
- Primary text: `#111827`
- Secondary text: `#6B7280`
- Muted text: `#9CA3AF`
- Primary accent: `#4F46E5`
- Accent hover: `#4338CA`
- Accent soft: `#EEF2FF`
- Success: `#22C55E`
- Warning: `#F59E0B`
- Danger: `#EF4444`
- Disabled background: `#F3F4F6`
- Disabled text: `#9CA3AF`
- Focus ring: `rgba(79, 70, 229, 0.18)`

Use color sparingly. Do not use color alone to communicate state.

## Spacing

- Base grid: 4px / 8px.
- Page padding: 16-20px desktop, 8-12px mobile.
- Column gap: 12-16px.
- Card padding: 12-16px.
- Form field gap: 8px.
- Section gap: 12-16px.
- Dense task/card internal gap: 6-8px.

## Radius

- Small controls: 8px.
- Cards and panels: 10px.
- Pills and progress tracks: 999px.
- Modals/drawers: 10-12px, except edge drawers may use square outer edges.

Avoid oversized rounded corners.

## Elevation

- Default surface: `0 1px 2px rgba(15, 23, 42, 0.04)`.
- Hover surface: `0 8px 20px rgba(15, 23, 42, 0.06)`.
- Overlay/drawer: `0 16px 40px rgba(15, 23, 42, 0.12)`.

Avoid heavy shadows, glow effects, glassmorphism, and decorative gradients.

## Layout

- Workspace layout uses three columns on desktop: compact sidebar, primary creation area, right output/task rail.
- Navigation and header should occupy minimal vertical space.
- Main content should receive maximum attention.
- Use whitespace and typography to group information before adding borders.
- On narrower screens, columns stack with consistent 12px gaps.

## Components

### Buttons

- Height: 36-38px for dense workspace controls.
- Radius: 8px.
- Border: 1px solid `#E5E7EB`.
- Secondary buttons: white background, subdued text.
- Primary button: accent background only for the one most important action in a group.
- Hover: subtle background/border change.
- Active: slight tonal change, no layout movement.
- Disabled: reduced opacity and disabled cursor.

### Inputs, Select, Textarea

- White or subtle background.
- 1px soft border.
- 36-38px minimum height for controls.
- Visible labels.
- Focus ring uses the accent token at low opacity.
- Textareas/editors should prioritize readability with mono font and comfortable line height.

### Cards and Panels

- White surface.
- Thin border.
- Very soft shadow.
- 8-10px radius.
- Avoid card-inside-card visual heaviness.

### Tables

- Soft separators.
- 12-13px dense text for metadata.
- Header background uses subtle surface.
- Hover uses subtle surface, not strong color.

### Badges and Status

- Small, low saturation.
- Use soft tinted backgrounds.
- Include text/icon where possible; do not rely only on color.

### Drawer/Dialog

- Clear scrim.
- White surface.
- Soft border and overlay shadow.
- Controls follow the same button/input tokens.

### Progress

- Compact status line.
- Neutral inactive state.
- Accent for current/running.
- Success for complete.
- No exaggerated animation.

## Motion

- Default transition: 150-180ms ease.
- Animate only color, opacity, border-color, box-shadow, and subtle background changes.
- Avoid transform motion that shifts layout.
- Respect `prefers-reduced-motion`.

## Accessibility

- Body text contrast should meet WCAG AA.
- Focus must be visible.
- Touch/click targets should be at least 36px in dense desktop UI, with larger spacing on mobile.
- Do not hide functionality behind hover-only affordances.
- Avoid horizontal overflow on mobile.

## Runtime Theme Engine

The application has one UI, one component system, one layout system, and one interaction system. Runtime themes must only change design tokens through CSS variables.

Theme switching must not change:

- Business logic
- API calls
- Component structure
- Layout positions
- User workflows
- Event handlers
- Data flow

Theme switching may change only:

- Color tokens
- Typography tokens
- Radius tokens
- Shadow/elevation tokens
- Border tokens
- Motion tokens
- Component visual tokens

The active theme is page-scoped and applied as `data-aicf-theme` on `body` and `documentElement`. Refreshing starts from the `linear` default so one project's appearance never carries into another project.

Available token packs:

- `linear`: light, minimal, professional default.
- `cursor`: dark developer workspace.
- `vercel`: minimal black/white SaaS.
- `glass`: elegant translucent surfaces, same layout.
- `ocean`: enterprise blue.
- `purple`: modern AI accent.
- `cyber`: dark neon developer atmosphere.
- `classic`: simple white.

All component rules should consume semantic variables such as `--surface`, `--border`, `--text-body`, `--accent`, `--radius`, `--shadow`, and `--motion`, rather than hardcoded visual values.
