# PropTech — Claude Context

## Project overview

Django 6.0.3 / Python 3.13 / GeoDjango project called **IstanbulPropTech**.
Primary purpose: interactive real-estate and amenity mapping for Istanbul.

Static files served via **WhiteNoise**. Development server: `python manage.py runserver`.
Settings: `IstanbulPropTech/settings.py`. Main URL config: `IstanbulPropTech/urls.py`.

---

## The newflow feature — what it is and where it lives

`/map/file/` is a self-contained multi-step flow for uploading JSON data files and
generating a shareable interactive Leaflet map. Three steps:

1. **Upload** (`GET /map/file/`) — user drops/selects JSON files, picks per-file icons, sets entry limit
2. **Configure** (`GET/POST /map/file/configure/`) — popup widget builder canvas, field selection
3. **Map** (rendered on configure POST) — final interactive Leaflet map

### Files

| File | Purpose |
|---|---|
| `newflow/__init__.py` | Empty package marker |
| `newflow/loader.py` | Core data loading — parses JSON/NDJSON, normalises records, computes distances |
| `newflow/views.py` | Two views: `file_map_view` + `file_map_configure_view` |
| `listings/templates/listings/file_map_upload.html` | Upload page |
| `listings/templates/listings/file_map_configure.html` | Widget builder canvas |
| `listings/templates/listings/file_map.html` | Final map output |
| `static/map-icons-master/` | Map icon font library (see below) |

URLs registered in `IstanbulPropTech/urls.py`:
```python
from newflow.views import file_map_view, file_map_configure_view
path("map/file/",           file_map_view,           name="file_map"),
path("map/file/configure/", file_map_configure_view, name="file_map_configure"),
```

---

## newflow/loader.py

Key functions:

- `parse_filename_coords(stem)` — parses filenames in the form `{lon}.{lat}.{query}.json`
  (e.g. `28.6478896.41.0185658.pharmacies.json`) → returns `(lon, lat, query)` or `(None, None, stem)`
- `_parse_content(content)` — handles standard JSON array, JSON object-of-objects, **and NDJSON**
  (one JSON object per line — falls back line-by-line silently skipping malformed lines)
- `normalize_record(record, idx, center_lat, center_lng)` — extracts name/lat/lng, all scalar fields,
  first 3 `user_reviews` items, computes `distance_m` via haversine from dataset centroid
- `load_from_upload(content, label, limit)` — called from the view for each uploaded file

`FIELD_MAP` handles aliased field names:
- name: `["title", "name"]`
- lat: `["latitude", "lat"]`
- lng: `["longtitude", "longitude", "lng", "lon"]` — note the typo "longtitude" is in the source data

---

## newflow/views.py

### `file_map_view` (GET/POST, `@csrf_exempt`)

**GET** — renders `file_map_upload.html`.

**POST** — processes each uploaded file:
- Parses `limit` from POST (default 10)
- For each file at index `i`:
  - Parses filename coords via `parse_filename_coords`
  - Calls `load_from_upload`
  - Reads `icon_{i}` from POST (the map-icon class chosen in the upload UI, e.g. `"map-icon-pharmacy"`)
  - Builds `amenities_data[label]`, `icon_choices[label]`
  - If filename has coords → adds to `origin_pins` list
- Stores everything in `request.session["newflow"]`
- Redirects to `file_map_configure`

Session keys stored:
```python
{
    "amenities_data":   dict,   # {label: [items...]}
    "icon_choices":     dict,   # {label: "map-icon-pharmacy"}
    "center_lat":       float,
    "center_lng":       float,
    "title":            str,    # " & ".join(labels)
    "available_fields": list,   # field names from first item (excluding internal fields)
    "sample_item":      dict,   # first item for the configure preview
    "origin_pins":      list,   # [{"lat":..., "lng":..., "label":...}, ...]
}
```

### `file_map_configure_view` (GET/POST)

**GET** — renders widget builder canvas (`file_map_configure.html`) with:
- `fields` — list of `{key, sample, default, locked}` dicts
- `sample_json` — JSON string of first item (used for live popup preview)
- `title`

**POST** — user submits field selection:
- `_BASE_FIELDS` are always force-injected server-side (they're `disabled` in the form so they don't POST)
- Renders `file_map.html` directly with full context

### Constants

```python
_INTERNAL_FIELDS = {"id", "lat", "lng", "distance_m", "name"}  # never shown in field picker

_BASE_FIELDS = {                                                  # always-on, locked in configure UI
    "title", "category", "distance", "link", "phone", "address",
    "web_site", "thumbnail", "review_count", "review_rating", "user_reviews"
}
```

---

## Upload page (file_map_upload.html)

Uses `{% load static %}` — loads `map-icons-master/dist/css/map-icons.css`.

### File accumulation
- Uses `DataTransfer` object (`dt`) to accumulate files across multiple picker/drop interactions
- Deduplicates by filename
- `renderCards()` rebuilds the card list and resyncs `input.files = dt.files`
- `removeFile(idx)` removes a file and re-indexes `iconChoices` dict

### Icon picker
- Each file card has a small **icon pick button** showing the current map-icon glyph
- Clicking opens a **modal** (`#picker-overlay`) with:
  - Search input (filters by icon slug)
  - 6-column grid of all ~150 map icons
  - Currently selected icon highlighted
- `iconChoices[idx]` stores the chosen class (e.g. `"map-icon-pharmacy"`)
- Default: `"map-icon-map-pin"` if not picked
- Hidden inputs `icon_0`, `icon_1`, … are kept in sync with file order and submitted with the form
- On `removeFile`, icon choices are re-indexed so order stays correct

### Submit button
- Disabled until files are added
- Label: "Generate map from {filename}" (single) or "Generate map from N files" (multiple)

### Record counting
- `FileReader` reads each file client-side, `countRecords()` parses it to show record count on the card
- Supports JSON array, JSON object-of-objects, and NDJSON

---

## Configure page (file_map_configure.html)

Split layout: LEFT (live preview + code block), RIGHT (field checkboxes).

- Base fields shown with "base" badge, `disabled` attribute — cannot be unchecked
- `MODIFIERS` set in JS: `['link', 'phone', 'address', 'web_site', 'thumbnail', 'review_count', 'review_rating', 'user_reviews']`
- Modifier fields enhance other fields' rendering rather than producing their own popup row
- Sample data loaded safely via `{{ sample_json|json_script:"sample-data" }}` + `JSON.parse(...)`
- Live preview mirrors all smart field behaviors

---

## Final map (file_map.html)

Uses `{% load static %}` — loads `map-icons-master/dist/css/map-icons.css`.

### Data injected via `json_script`
```html
{{ amenities_data|json_script:"amenities-data" }}
{{ field_config|json_script:"field-config-data" }}
{{ origin_pins|json_script:"origin-pins-data" }}
{{ icon_choices|json_script:"icon-choices-data" }}
```
(`json_script` is used throughout to avoid XSS / Unicode injection issues with `|safe`)

### Markers

Each dataset gets a **pin-shaped divIcon**:
- Colored circle (from `PALETTE`) with the dataset's chosen map-icon glyph centered inside
- Downward triangle tip below the circle
- CSS classes: `.mi-pin`, `.mi-pin-circle`, `.mi-pin-tip`
- `iconAnchor: [21, 48]` — tip of triangle anchors to the lat/lng point
- `iconCls = iconChoices[type] || 'map-icon-map-pin'`

### "You are here" origin pins

Parsed from filenames (`{lon}.{lat}.{query}.json`). Rendered as pulsing indigo dot + label tag.

**Popup** — `.origin-popup` class gives it distinct styling (purple gradient header, indigo border/shadow):
- Header: gradient background, "📍 You are here", pin label, coordinates
- Body: "Nearest from each list" — one row per dataset showing:
  - Dataset's colored circle + map-icon glyph
  - Name of nearest item (haversine computed in JS from pin coords to each item)
  - Dataset type label + distance in bold indigo
  - Sorted closest-first

Haversine computed in JS (`haversineM()`) at popup-open time — independent of server-side centroid distances.

### Popup widget (`buildPopup`)

Field render order:
1. **Title** — plain or wrapped in `<a href="{link}">` if link field is present
2. **Meta row** — category pill badge (left) + distance (right) as one flex row
3. **Thumbnail** — full-width image with floating rating chip (`⭐ rating · count`) overlay
4. **Icon bar** — 4 round colored buttons: call (`tel:`), WhatsApp (`wa.me/`), address (hover tooltip), website
5. **Extra fields** — any non-base, non-modifier fields selected in configure
6. **Reviews** — summary line + collapsible list of up to 3 reviews with avatar, name, rating, snippet

Review avatar field lookup (Google Maps capitalized format first): `r.Name || r.name || r.author`
Review pic: `r.ProfilePicture || r.profile_picture || r.avatar`

Phone numbers are cleaned to digits-only for `tel:` and `wa.me/` links.
Thumbnail URLs starting with `//` get `https:` prepended.

### Legend

Left panel, collapsible. Each entry shows the dataset's colored circle + map-icon glyph + label + count.
Lotfinity branding in footer.

### Color palette
```js
const PALETTE = ['#6366f1','#f59e0b','#10b981','#ef4444','#8b5cf6','#ec4899','#14b8a6','#f97316'];
```

---

## map-icons library

Path: `static/map-icons-master/`
CSS: `static/map-icons-master/dist/css/map-icons.css`
Font files: `static/map-icons-master/dist/fonts/` (eot, ttf, woff, svg)

Usage: `<span class="map-icon map-icon-pharmacy"></span>`

The `dist/js/map-icons.js` is a **Google Maps** integration — it is **not used** here.
For Leaflet we use the CSS icon font directly inside `L.divIcon` HTML strings.

All ~150 icon slugs are listed in `ALL_ICONS` in the upload page JS.

---

## Key bugs fixed (don't repeat these)

| Bug | Fix |
|---|---|
| `input.value = ''` wiped `input.files` after `DataTransfer` assignment | Removed that line — never clear input after assigning `dt.files` |
| `{{ sample_json\|safe }}` in `<script>` crashed on Turkish Unicode / `</script>` strings | Use `json_script` tag + `JSON.parse(getElementById(...).textContent)` |
| NDJSON files threw `JSONDecodeError Extra data line 2` | `_parse_content()` falls back to line-by-line parsing |
| Reviews showed "Anonymous" | Google Maps scraper uses capitalized keys — lookup `r.Name` before `r.name` |
| Multiple file upload produced `JSON.parse` error | Was caused by `|safe` embedding two JSON blobs — fixed by `json_script` |
| Icon choices lost when a file is removed | `removeFile()` re-indexes `iconChoices` dict before rebuilding cards |

---

## What's working end-to-end

- Upload page: drag & drop, file picker, per-file icon selection modal, record counting, limit setting
- Filename coordinate parsing → "You are here" origin pins with pulsing animation
- Configure page: live popup preview, base fields locked, modifier fields hidden from picker
- Final map: custom pin-shaped markers with chosen map-icons glyph per dataset
- Origin pin popup: nearest item from each dataset with icon + distance, sorted by proximity
- Legend: icon + color per dataset, collapsible
- Popup: full design — title link, category+distance row, thumbnail+rating chip, icon bar (call/WA/address/web), reviews collapsible

## Possible next things (not started)

- Show parsed filename coords in file cards on the upload page (client-side JS filename parsing for immediate feedback)
- Export / share the final map as a standalone HTML file
- Color picker per dataset (currently uses fixed palette)
