# Embedding the configuration page in the HomeServer visualisation

`config-page.html` is a single self-contained HTML page (no external
assets) that talks to the HomeServer's own data-point API. Embedding
it in a Gira visualisation gives end users a runtime configuration UI
they can open from a tile on the HomeServer homepage — no Experte
access required.

## Step 1 — Determine the data-point API path on your HomeServer

The HomeServer exposes its data points over an HTTP/JSON API at a path
that varies by firmware. Find the right one for your installation:

1. Open the HomeServer visualisation in a browser.
2. Open the browser developer tools, *Network* tab.
3. Click any widget that reads or writes a data point (a switch, a
   slider, …).
4. Note the URL of the request. Examples seen on different firmware
   generations:
   - `/endpoints/datapoints/<name>` — HS 4.10+
   - `/quad/json/dp/<name>` — older HS
   - `/hslf/json/dp/<name>` — alternate

5. Open `config-page.html` and edit the three `URL` constants near the
   top to match. The defaults assume `/endpoints/datapoints/`.

## Step 2 — Host the HTML in the visualisation

The HomeServer Experte gives several options for hosting custom HTML.
Pick whichever your version supports.

### Option A — HTML widget inside a visualisation page

If your Experte supports HTML/iframe widgets in visualisation pages
(common in HS 4.10+):

1. In the Experte, open the visualisation page that should host the
   config UI.
2. Add an HTML widget.
3. Paste the entire contents of `config-page.html` into the widget's
   HTML field, OR set the widget's `src` to a hosted URL of the file
   (see Option B).
4. Save and download.

### Option B — Static file in the HomeServer's web root

If your Experte supports adding static assets to the HomeServer:

1. *Datei → Datei in Projekt importieren* (or similar). Add
   `config-page.html`.
2. The HomeServer exposes it at a path like
   `http://<homeserver-ip>/files/config-page.html`.
3. Either link to that URL from the homepage, or embed it via iframe:

```html
<iframe src="/files/config-page.html"
        style="width:100%; height:100%; border:0"
        title="Sonos Configuration"></iframe>
```

### Option C — Visualisation tile that opens the URL in a new tab

If neither A nor B is available, place an HTML link element on the
homepage that opens `http://<homeserver>/files/config-page.html` in a
new tab.

## Step 3 — Pre-create the data points

The configuration page reads `sonos.*` data points and writes to them.
It does **not** create new data points on the fly — the HomeServer
data-point API requires data points to exist beforehand.

Before adding a player or station via the UI, the matching data points
must exist in the Experte. Pre-create slots:

- One set of `sonos.<name>.*` per player you might want to add (see
  `experte-blocks/data-points.md`). A common pattern is to pre-create
  8 slots named `room1` … `room8`, then rename them via the UI on first
  configuration.
- One set of `sonos.station.<n>.*` per station slot (1..16 is a good
  default).

A friendly error message appears in the UI if a write hits a missing
data point.

## Step 4 — Wire up the "re-subscribe" command

The page exposes a *Re-subscribe all* button which writes `true` to a
boolean data point `sonos.cmd.resubscribe`. Create that data point and
wire a logic block:

| Property | Value |
| --- | --- |
| Name     | `sonos.lb.onResubscribeCmd` |
| Trigger  | `sonos.cmd.resubscribe` rising edge |
| Actions  | For each player, run `sonos.<name>.subscribe-av` and `sonos.<name>.subscribe-rc`, then reset `sonos.cmd.resubscribe` to `false` |

## Step 5 — Verify

1. Open the visualisation in a browser.
2. Navigate to the page hosting `config-page.html`.
3. The status bar at the top should read "Updated HH:MM:SS".
4. Add a player by typing a name + IP; confirm the new row appears.
5. Add a radio station; trigger a KNX button bound to that station;
   confirm the player starts.

## Security note

The data-point API is normally protected by the HomeServer's
authentication. The page uses `credentials: 'same-origin'` so it
inherits the browser's session. Do **not** disable HomeServer auth.
If you expose the visualisation to the internet, ensure the auth is
enabled.
