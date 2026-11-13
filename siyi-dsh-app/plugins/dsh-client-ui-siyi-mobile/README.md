# dsh-client-ui-siyi-mobile

Mobile layout patch for DeepSeek Harness on narrow screens.

## What it fixes

- **Icon rail**: on phones the left rail eats 12–14% of the viewport, which
  makes the main column cramped. This plugin marks `<html>` with
  `data-windows-titlebar`, which makes the layout compute the collapsed rail
  width as 0 — the sidebar becomes a drawer, matching the official DSH mobile
  behaviour.
- **Settings panel**: at 376 px viewport width the panel's fixed 188 px nav
  column leaves ~140 px for content, forcing Chinese text to wrap one
  character per line. On `max-width: 720px` the panel switches to a single
  column with the nav as a horizontal scrolling tab strip.
- **Dialogs**: raises the generic dialog max-width so buttons are reachable.

## Structure

| Path | Purpose |
|---|---|
| `lib/index.js` | Server half. Registers nothing; present because a client plugin package needs a `main` entry. |
| `lib/client.js` | Browser half. Injects one `<style>` and keeps the `<html>` attribute alive via `MutationObserver`. |

## Install

Ship this package inside the profile's `node_modules` and reference it from
`cordis.patch.yml`:

```yaml
- insert:
    - id: siyi-mobile-layout
      name: 'dsh-client-ui-siyi-mobile'
```

Then restart so the browser half is re-fetched.

## Caveats

The stylesheet targets hashed class names produced by the official build
(`.VOzbGW_panel`, `.pI_x6G_sidebarCol`, `.hHd-Xa_toggle`). A DSH upgrade that
regenerates those hashes silently disables the patch — the styles simply stop
matching. Re-check this file after every upstream bump.

## License

MIT
