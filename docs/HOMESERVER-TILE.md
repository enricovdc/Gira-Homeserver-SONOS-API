# Adding the Sonos Admin tile to the HomeServer landing page

The Gira FacilityServer / HomeServer 4 landing page at
`http://<homeserver-ip>/main-site/` (or `/`) is a static HTML template
with tiles grouped under headings like *Information*, *Configurations*,
*Diagnostics*, *System*. Each tile is one `<a class="box">` element.
You can add a tile that opens the Sonos Admin web UI in one click.

There are three paths, in order of integrator effort:

## Path 1 — Iframe-embed the standalone tile page

The Admin block exposes `/tile.html` which serves a fully Gira-styled
single-tile page. Embed it as an iframe inside any HomeServer
visualisation page that supports an HTML widget:

```html
<iframe src="http://<homeserver-ip>:8080/tile.html"
        style="border: 0; width: 100%; height: 320px;"
        title="Sonos Admin">
</iframe>
```

The tile mimics the Gira look (white container, dark-grey "call-up"
text, hover state). Click it and the Admin UI opens in a new tab.

## Path 2 — Paste the tile fragment into `/main-site/index.html`

The Admin block also exposes `/tile.fragment` which returns the bare
`<a class="box">` HTML, ready to drop into the existing
`/main-site/index.html` template.

1. Visit `http://<homeserver-ip>:8080/tile.fragment` in a browser.
   Copy the entire fragment.
2. Use the Gira Experte's file-management feature (or any FacilityServer
   admin tooling you have) to open `/main-site/index.html`.
3. Find the `<div id="config_items" class="box_list">` block. Paste the
   fragment as the last child. Remove the `box_last` class from whatever
   was previously last so the visual border ends in the right place.
4. Save. Reload `http://<homeserver-ip>/`. The new "Sonos Admin" tile
   now appears in the Configurations group.

> The `<img class="box_img" src=".../icon.svg">` reference points back
> at the Admin's `/icon.svg` endpoint so no separate icon file needs to
> live in `/main-site/`. The tile's `href` opens the Admin UI in a new
> tab.

## Path 3 — Surface the URL via a HomeServer logic block

If you can't modify the landing page HTML at all but can still place
visualisation widgets, write a one-shot logic block that reads
`http://localhost:8080/info` once at HS startup and writes the
`adminUrl` field into a string data point bound to a visualisation
text widget. The end user clicks the URL.

## Quick reference of the endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /` | Full Sonos Admin web UI (Gira-styled). |
| `GET /info` | JSON with `adminUrl`, `listenPort`, registry counts. |
| `GET /tile.fragment` | Bare `<a class="box">` HTML for `/main-site/index.html`. |
| `GET /tile.html` | Standalone Gira-styled page containing one tile. |
| `GET /icon.svg` | Speaker icon used by the tile. |
| `GET /api/players` | JSON list with `zoneName`, `name`, `ip`, `mac`, `uuid`, `model`, `source`. |
| `GET /api/stations` | JSON list of configured radio stations. |
| `GET /api/cloud` | OAuth state (client secret is never echoed). |

## Why three paths?

- **Path 1** needs no filesystem access — works whenever the
  visualisation supports an HTML widget.
- **Path 2** gives the cleanest result (a real Gira tile alongside the
  built-in ones) but needs write access to `/main-site/index.html`.
- **Path 3** works on the most restricted HS configurations but takes
  the most setup.
