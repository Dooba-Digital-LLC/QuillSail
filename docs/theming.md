# Themes

Open **Settings → Appearance** to design the publication without editing files. Start with Classic, Broadsheet, Brief, Reference or Canvas. These are original, configurable styles, not copies of another publication's identity. Loading a starting style fills the visual controls; it does not save settings or change content. Existing installations retain their previous styling until a design is saved.

Upload a main publication logo, an optional dark-background variant, an optional email logo, and a square publication icon. PNG, JPEG, WebP and GIF are supported and re-encoded to remove metadata. Transparent PNG is recommended for logos. The email and dark variants fall back to the main logo. Publication branding is independent of QuillSail's product logo and footer credit.

In **Homepage hero**, choose title text, a logo, or both for the top of the homepage. Set a custom title, subtitle and small overline, or leave the fields blank to reuse the publication name, tagline and organization. The subtitle and overline can also be hidden. Upload separate hero logos for light and dark backgrounds, or reuse the publication logo, and choose the logo width. **Hero alignment** places the hero at the left, center or right; the default keeps newsletter homepages centered and blog homepages left-aligned. Logo-only mode falls back to the title if no logo is available. These controls work for newsletter and blog homepages without changing navigation, publication metadata or email branding. Choose **Preview homepage** to review changes at desktop and mobile sizes, then save the publication design. Loading a style preset preserves these hero settings. Subscription buttons are disabled in design previews.

Outgoing messages embed the configured publication logo as an inline PNG attachment referenced by content ID. This avoids an external download for the logo and converts WebP logos to a widely supported email format. Browser previews still use the hosted image. Previously delivered messages retain their original remote image URLs; generate a new test email after upgrading to check the embedded version in your mail app.

Choose body and heading typefaces, custom font stacks, light and dark colors, widths, text sizes, line spacing, masthead alignment, story cards or columns, and email presentation. Custom font stacks use fonts available on the reader's device; they do not download font files. Readers can still override the website's default color mode. Email clients have their own font, dark-mode and CSS behavior.

Use **Preview changes** to view a private sample, switch between website, dashboard and email, and compare desktop and mobile. The dashboard preview uses sample counts and disabled controls. Previewing does not persist settings or uploaded images, publish content, queue jobs, or send email. Without JavaScript, load a style and use the preview buttons manually. Save applies the design to public pages, the dashboard and future emails; it cannot restyle messages already delivered.

The dashboard shares your publication logo and icon, light/dark palette, body and heading fonts, spacing and corner style. It keeps its navigation, tables and editing workspace arranged for administration. Homepage hero settings, reading widths, story columns and email presentation apply to their respective publication views. Dashboard controls keep usable sizes and adjust text contrast for the selected backgrounds. The saved default color mode applies to both the website and dashboard; the theme button can select a personal override.

If a custom CSS rule hides the editor, open `/dashboard/settings/appearance?dashboard_style=default` under your installation's URL prefix. This uses the default dashboard styles for that request without deleting the saved design. The same permission and sign-in requirements apply. Edit the offending rule and save to return to the configured design.

**Reuse a design** exports saved appearance settings as JSON. Imports load settings into the editor for review before saving. Logos travel separately; design files cannot change accounts, email credentials or other site settings. A referenced installed template theme must also exist on the receiving installation.

Advanced controls accept website/dashboard CSS and separate newsletter email CSS. Shared website hooks include `.publication`; dashboard pages also have `.dashboard`. Use `.publication:not(.dashboard)` for public-only rules and `.dashboard` for administration-only rules. Other website hooks include `.site-header`, `.issue-header`, `.issue-story-list`, `.issue-block` and `.prose`. Dashboard hooks include `.dash-sidebar`, `.dash-content`, `.panel` and `.dash-nav`. Email hooks include `.email-shell`, `.email-masthead`, `.email-content`, `.email-story` and `.email-footer`. Email CSS is inlined offline; external `@import` rules are ignored. Use `!important` when overriding generated email declarations. Use simple email-compatible properties, and verify received messages in your target email clients before a real send. Installed theme styles also apply to the dashboard; preserve visible navigation, labels, errors and keyboard focus when customizing it.

```css
/* Website */
.publication .issue-header h1 { letter-spacing: -0.03em; }
.publication .issue-block { border-top: 3px double var(--border); }
```

```css
/* Newsletter email */
.email-story h2 { color: #6236a0 !important; }
.email-content { border-radius: 0 !important; }
```

For complete markup control, custom web fonts served by your installation, or reusable changes beyond the designer, create a folder such as `themes/campus/` beside `copydesk.toml`:

```text
themes/campus/
  theme.toml
  static/campus.css
  templates/public/home.html
```

`theme.toml`:

```toml
name = "campus"
title = "Campus"
version = "1.0.0"
parent = "default"
stylesheets = ["campus.css"]
```

Restart the server so the theme is discovered, then select it under appearance settings. The theme's CSS is served from its static directory. To override a template, use the same path as under `src/copydesk/templates/`, for example `public/home.html` or `layout/public.html`. Templates resolve from the selected theme, through its parent chain, then extensions, then core. Omit templates you do not need to change. Bundled Default and Broadsheet themes provide examples.

Preserve CSRF fields, navigation, form errors, accessible labels, and CSP nonces when overriding templates. Prefer external CSS/JavaScript; inline code needs the supplied `csp_nonce()` helper. Themes can override email templates too; verify inline styles and plain-text equivalents with test messages. Template code is trusted server-side code and should be reviewed before installation.
