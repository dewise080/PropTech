import json

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .loader import load_from_upload, parse_filename_coords

DEFAULT_LIMIT = 10

# Fields that are internal to the map engine — never shown in the field picker
_INTERNAL_FIELDS = {"id", "lat", "lng", "distance_m", "name"}

# Always on, cannot be unchecked — the structural base of every popup
_BASE_FIELDS = {"title", "category", "distance", "link", "phone", "address", "web_site", "thumbnail",
                "review_count", "review_rating", "user_reviews"}


@csrf_exempt
@require_http_methods(["GET", "POST"])
def file_map_view(request: HttpRequest) -> HttpResponse:
    """
    GET  — render the upload form.
    POST — process uploaded JSON files, store in session, redirect to configure step.
    """
    if request.method == "GET":
        return render(request, "listings/file_map_upload.html")

    uploaded_files = request.FILES.getlist("files")
    if not uploaded_files:
        return HttpResponseBadRequest("Upload at least one JSON file.")

    try:
        limit = max(1, int(request.POST.get("limit", DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    amenities_data: dict = {}
    icon_choices: dict = {}
    all_lats: list[float] = []
    all_lngs: list[float] = []
    labels: list[str] = []
    origin_pins: list[dict] = []   # explicit coords parsed from filenames

    for i, upload in enumerate(uploaded_files):
        stem  = upload.name.rsplit(".", 1)[0]
        content = upload.read().decode("utf-8")

        # Try to extract origin coords from filename
        lon, lat, query = parse_filename_coords(stem)
        label = query if lon is not None else stem

        data = load_from_upload(content, label=label, limit=limit)
        if not data["items"]:
            continue

        icon_class = request.POST.get(f"icon_{i}", "map-icon-map-pin") or "map-icon-map-pin"

        amenities_data[data["label"]] = data["items"]
        icon_choices[data["label"]] = icon_class

        if lon is not None:
            # Use filename coords as this file's center
            origin_pins.append({"lng": lon, "lat": lat, "label": query.replace("_", " ").title()})
            all_lats.append(lat)
            all_lngs.append(lon)          # intentional: lon stored for lng average
        else:
            all_lats.append(data["center"]["lat"])
            all_lngs.append(data["center"]["lng"])

        labels.append(label.replace("_", " ").title())

    if not amenities_data:
        return HttpResponseBadRequest("No valid records with name and coordinates found in any uploaded file.")

    center_lat = sum(all_lats) / len(all_lats)
    center_lng = sum(all_lngs) / len(all_lngs)

    # Derive available fields from the first item across all datasets
    first_item = next(iter(amenities_data.values()))[0]
    available_fields = [k for k in first_item if k not in _INTERNAL_FIELDS]

    # Store everything in session for the configure step
    request.session["newflow"] = {
        "amenities_data":   amenities_data,
        "icon_choices":     icon_choices,
        "center_lat":       center_lat,
        "center_lng":       center_lng,
        "title":            " & ".join(labels),
        "available_fields": available_fields,
        "sample_item":      first_item,
        "origin_pins":      origin_pins,
    }

    return redirect("file_map_configure")


@require_http_methods(["GET", "POST"])
def file_map_configure_view(request: HttpRequest) -> HttpResponse:
    """
    GET  — show the popup widget builder canvas.
    POST — render the final map using the field selection from the builder.
    """
    session_data = request.session.get("newflow")
    if not session_data:
        return redirect("file_map")

    if request.method == "POST":
        # Base fields are disabled in the form so won't POST — force-include them
        selected_fields = list(_BASE_FIELDS) + [
            f for f in request.POST.getlist("fields") if f not in _BASE_FIELDS
        ]

        amenities_data = session_data["amenities_data"]
        context = {
            "query":         session_data["title"],
            "center_lat":    session_data["center_lat"],
            "center_lng":    session_data["center_lng"],
            "radius_m":      0,
            "amenities_data": amenities_data,
            "field_config":   selected_fields,
            "origin_pins":    session_data.get("origin_pins", []),
            "icon_choices":   session_data.get("icon_choices", {}),
        }
        return render(request, "listings/file_map.html", context)

    # GET — widget builder
    sample_item = session_data["sample_item"]
    available_fields = session_data["available_fields"]

    # Build field list: virtual fields first, then data fields
    # "title" and "distance" are virtual (always exist, not from raw JSON keys)
    virtual = ["title", "distance"]
    data_fields = [f for f in available_fields if f not in {"title", "distance"}]
    all_fields = virtual + data_fields

    fields_for_template = []
    for f in all_fields:
        if f == "title":
            sample_val = sample_item.get("name", "")
        elif f == "distance":
            sample_val = "auto-computed"
        else:
            sample_val = sample_item.get(f, "")

        fields_for_template.append({
            "key":     f,
            "sample":  str(sample_val)[:80] if sample_val else "",
            "default": True if f in _BASE_FIELDS else False,
            "locked":  f in _BASE_FIELDS,
        })

    context = {
        "fields":      fields_for_template,
        "sample_json": json.dumps(sample_item, ensure_ascii=False, default=str),
        "title":       session_data["title"],
    }
    return render(request, "listings/file_map_configure.html", context)
