from typing import Any, Dict, List, Tuple
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlparse
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.contrib.gis.db.models.functions import Distance, Transform
from django.contrib.gis.geos import Point
from django.db import connection, reset_queries
from django.conf import settings
from django.views.decorators.http import require_http_methods

from .models import Listing, DisplayConfig, NearbyAmenityConfig
from .services import ClosestStoresService
from transit_layer.models import BusStop, MetroStation, MetrobusStation, TaxiStand
from stores_layer.models import Clothing, Grocery, Mall, Park
from education_layer.models import School

# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# ============================================================================
# SIMPLIFIED VIEW CONFIGURATION - Adjust these numbers to control view output
# ============================================================================
NUM_LISTINGS = 6  # Number of listings to display
NUM_CLOSEST_STATIONS = 3  # Number of closest metro stations per listing
NUM_CLOSEST_GROCERY_STORES = 3  # Number of closest grocery stores
NUM_CLOSEST_CLOTHING_STORES = 3  # Number of closest clothing stores
# ============================================================================


def _listing_feature(listing: Listing) -> Dict[str, Any]:
    """
    Convert a Listing to GeoJSON feature with stores and transit data.
    Uses pre-computed cached closest stores for performance.
    """
    feature_start = time.time()
    queries_before = len(connection.queries) if settings.DEBUG else 0
    
    try:
        # Find the nearest metro station with Distance annotation (meters due to geography=True)
        metro_start = time.time()
        nearest = (
            MetroStation.objects.annotate(distance=Distance("location", listing.location))
            .order_by("distance")
            .first()
        )
        metro_time = time.time() - metro_start

        closest_name = nearest.name if nearest else None
        distance_m = float(nearest.distance.m) if nearest and nearest.distance is not None else None

        logger.debug(
            f"[METRO] Listing {listing.id} ({listing.title}): "
            f"Found nearest station '{closest_name}' at {distance_m}m | Time: {metro_time:.4f}s"
        )

        # Get pre-computed closest stores from cache
        cache_start = time.time()
        closest_grocery_ids, closest_clothing_ids = ClosestStoresService.get_cached_stores(listing)
        cache_time = time.time() - cache_start
        
        logger.debug(
            f"[CACHE] Listing {listing.id}: "
            f"Retrieved {len(closest_grocery_ids)} grocery, {len(closest_clothing_ids)} clothing | "
            f"Time: {cache_time:.4f}s"
        )

        geom = listing.location
        
        # Get up to 3 images for the carousel
        images_start = time.time()
        listing_images = list(
            listing.images.order_by('order')[:3]
        )
        images_time = time.time() - images_start
        
        # Build images array - include primary image and then listing images
        images = []
        if listing.image:
            images.append(listing.image.url)
        for img in listing_images:
            images.append(img.image.url)
        # Deduplicate and limit to 3
        images = list(dict.fromkeys(images))[:3]
        
        logger.debug(
            f"[IMAGES] Listing {listing.id}: "
            f"Retrieved {len(images)} images | Time: {images_time:.4f}s"
        )
        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [geom.x, geom.y],
            },
            "properties": {
                "id": listing.id,
                "title": listing.title,
                "price": listing.price,
                "size_sqm": listing.size_sqm,
                "closest_station_name": closest_name,
                "distance_to_station_m": distance_m,
                "closest_grocery_store_ids": closest_grocery_ids,
                "closest_clothing_store_ids": closest_clothing_ids,
                "image_url": listing.image.url if listing.image else None,
                "images": images,
            },
        }
        
        feature_time = time.time() - feature_start
        queries_after = len(connection.queries) if settings.DEBUG else 0
        logger.debug(
            f"[FEATURE_COMPLETE] Listing {listing.id}: "
            f"Total time: {feature_time:.4f}s | "
            f"Queries: {queries_after - queries_before} | "
            f"Breakdown - Metro: {metro_time:.4f}s, Cache: {cache_time:.4f}s"
        )
        
        return feature
        
    except Exception as e:
        logger.error(f"[ERROR] Failed to create feature for listing {listing.id}: {str(e)}", exc_info=True)
        raise


def map_view(request: HttpRequest) -> HttpResponse:
    return render(request, "listings/map_view_mob.html")


def listings_geojson(request: HttpRequest) -> JsonResponse:
    """
    Main endpoint that returns GeoJSON features for all listings.
    Includes comprehensive performance monitoring and debug logging.
    """
    request_start = time.time()
    
    # Reset queries if in DEBUG mode for accurate tracking
    if settings.DEBUG:
        reset_queries()
    
    try:
        logger.info("=" * 80)
        logger.info("[API_START] listings_geojson endpoint called")
        logger.info(f"[CONFIG] DEBUG mode: {settings.DEBUG}")
        
        # Get display configuration
        config_start = time.time()
        config = DisplayConfig.get_config()
        config_time = time.time() - config_start
        
        logger.info(
            f"[CONFIG_LOADED] Time: {config_time:.4f}s | "
            f"Max Listings: {config.max_listings}"
        )
        
        # Query listings
        query_start = time.time()
        total_listings = Listing.objects.count()
        listings = Listing.objects.all()[: config.max_listings]
        listings_list = list(listings)  # Force evaluation
        query_time = time.time() - query_start
        actual_count = len(listings_list)
        
        logger.info(
            f"[DB_QUERY] Retrieved {actual_count}/{total_listings} listings | "
            f"Query time: {query_time:.4f}s"
        )
        
        if settings.DEBUG:
            queries_after_listings = len(connection.queries)
            logger.debug(f"[DB_STATS] Queries so far: {queries_after_listings}")
        
        # Process features
        features_start = time.time()
        features: List[Dict[str, Any]] = []
        
        for idx, listing in enumerate(listings_list, 1):
            try:
                feature = _listing_feature(listing)
                features.append(feature)
                
                # Progress logging every 10 listings
                if idx % 10 == 0:
                    logger.info(f"[PROGRESS] Processed {idx}/{actual_count} listings")
                    
            except Exception as e:
                logger.error(f"[FEATURE_FAILED] Listing {listing.id} failed: {str(e)}")
                # Continue processing other listings
                continue
        
        features_time = time.time() - features_start
        
        logger.info(
            f"[FEATURES_PROCESSED] Processed {len(features)} features | "
            f"Time: {features_time:.4f}s | "
            f"Avg per feature: {features_time/len(features):.4f}s" if features else ""
        )
        
        # Build response
        response_data = {"type": "FeatureCollection", "features": features}
        
        # Get query statistics
        if settings.DEBUG:
            total_queries = len(connection.queries)
            logger.debug(f"[DB_STATS] Total queries executed: {total_queries}")
            
            # Calculate total query time
            total_query_time = sum(float(q.get("time", 0)) for q in connection.queries)
            logger.debug(f"[DB_STATS] Total query time: {total_query_time:.4f}s")
        
        total_time = time.time() - request_start
        
        logger.info(
            f"[API_COMPLETE] ✓ Success | "
            f"Total time: {total_time:.4f}s | "
            f"Features returned: {len(features)}/{actual_count} | "
            f"Response size: ~{len(str(response_data))/1024:.2f}KB"
        )
        logger.info("=" * 80)
        
        return JsonResponse(response_data)
        
    except Exception as e:
        total_time = time.time() - request_start
        logger.error(
            f"[API_FAILED] ✗ Error after {total_time:.4f}s: {str(e)}",
            exc_info=True
        )
        logger.info("=" * 80)
        
        # Return error response
        return JsonResponse({
            "type": "FeatureCollection",
            "features": [],
            "error": str(e)
        }, status=500)


# ============================================================================
# SIMPLIFIED VIEW - Shows limited listings with closest stations and stores
# ============================================================================

def simplified_map_view(request: HttpRequest) -> HttpResponse:
    """Render the simplified map view template with limited data.
    If settings.SIMPLIFIED_INLINE_DATA is True, embed the GeoJSON into the page.
    """
    context: Dict[str, Any] = {}
    try:
        if getattr(settings, "SIMPLIFIED_INLINE_DATA", False):
            data = generate_simplified_geojson()
            # Serialize to JSON for safe embedding in JS
            context["inline_geojson_json"] = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        logger.error(f"[SIMPLIFIED_INLINE_ERROR] Failed to inline data: {str(e)}", exc_info=True)
    return render(request, "listings/map_view_simplified.html", context)


def _simplified_listing_feature(listing: Listing) -> Dict[str, Any]:
    """
    Convert a Listing to GeoJSON feature with limited closest stations and stores.
    Used for the simplified view.
    """
    try:
        # Get closest metro stations (limited)
        stations_start = time.time()
        closest_stations = (
            MetroStation.objects.annotate(distance=Distance("location", listing.location))
            .order_by("distance")
            .values("id", "name", "distance")[: NUM_CLOSEST_STATIONS]
        )
        stations_list = list(closest_stations)
        stations_time = time.time() - stations_start
        
        logger.debug(
            f"[SIMPLIFIED] Listing {listing.id}: "
            f"Found {len(stations_list)} closest stations | Time: {stations_time:.4f}s"
        )
        
        # Get closest grocery stores (limited)
        grocery_start = time.time()
        closest_groceries = (
            Grocery.objects.annotate(distance=Distance("location", listing.location))
            .order_by("distance")
            .values("id", "name", "distance")[: NUM_CLOSEST_GROCERY_STORES]
        )
        groceries_list = list(closest_groceries)
        grocery_time = time.time() - grocery_start
        
        logger.debug(
            f"[SIMPLIFIED] Listing {listing.id}: "
            f"Found {len(groceries_list)} closest grocery stores | Time: {grocery_time:.4f}s"
        )
        
        # Get closest clothing stores (limited)
        clothing_start = time.time()
        closest_clothing = (
            Clothing.objects.annotate(distance=Distance("location", listing.location))
            .order_by("distance")
            .values("id", "name", "distance")[: NUM_CLOSEST_CLOTHING_STORES]
        )
        clothing_list = list(closest_clothing)
        clothing_time = time.time() - clothing_start
        
        logger.debug(
            f"[SIMPLIFIED] Listing {listing.id}: "
            f"Found {len(clothing_list)} closest clothing stores | Time: {clothing_time:.4f}s"
        )
        
        # Convert distances from meters to the format needed
        stations_data = [
            {
                "id": s["id"],
                "name": s["name"],
                "distance_m": float(s["distance"].m) if s["distance"] else None,
                "location": {
                    "type": "Point",
                    "coordinates": [0, 0]  # Placeholder, will be fetched properly
                }
            }
            for s in stations_list
        ]
        
        # Fetch actual coordinates for stations
        for idx, station_data in enumerate(stations_data):
            try:
                station = MetroStation.objects.get(id=station_data["id"])
                station_data["location"] = {
                    "type": "Point",
                    "coordinates": [station.location.x, station.location.y]
                }
            except MetroStation.DoesNotExist:
                pass
        
        groceries_data = [
            {
                "id": g["id"],
                "name": g["name"],
                "distance_m": float(g["distance"].m) if g["distance"] else None,
                "location": {
                    "type": "Point",
                    "coordinates": [0, 0]  # Placeholder, will be fetched properly
                }
            }
            for g in groceries_list
        ]
        
        # Fetch actual coordinates for grocery stores
        for idx, grocery_data in enumerate(groceries_data):
            try:
                grocery = Grocery.objects.get(id=grocery_data["id"])
                grocery_data["location"] = {
                    "type": "Point",
                    "coordinates": [grocery.location.x, grocery.location.y]
                }
            except Grocery.DoesNotExist:
                pass
        
        clothing_data = [
            {
                "id": c["id"],
                "name": c["name"],
                "distance_m": float(c["distance"].m) if c["distance"] else None,
                "location": {
                    "type": "Point",
                    "coordinates": [0, 0]  # Placeholder, will be fetched properly
                }
            }
            for c in clothing_list
        ]
        
        # Fetch actual coordinates for clothing stores
        for idx, clothing_item in enumerate(clothing_data):
            try:
                clothing = Clothing.objects.get(id=clothing_item["id"])
                clothing_item["location"] = {
                    "type": "Point",
                    "coordinates": [clothing.location.x, clothing.location.y]
                }
            except Clothing.DoesNotExist:
                pass
        
        geom = listing.location
        
        # Get up to 3 images for the carousel
        images_start = time.time()
        listing_images = list(
            listing.images.order_by('order')[:3]
        )
        images_time = time.time() - images_start
        
        # Build images array - include primary image and then listing images
        images = []
        if listing.image:
            images.append(listing.image.url)
        for img in listing_images:
            images.append(img.image.url)
        # Deduplicate and limit to 3
        images = list(dict.fromkeys(images))[:3]
        
        logger.debug(
            f"[SIMPLIFIED_IMAGES] Listing {listing.id}: "
            f"Retrieved {len(images)} images | Time: {images_time:.4f}s"
        )
        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [geom.x, geom.y],
            },
            "properties": {
                "id": listing.id,
                "title": listing.title,
                "price": listing.price,
                "size_sqm": listing.size_sqm,
                "image_url": listing.image.url if listing.image else None,
                "images": images,
                "closest_stations": stations_data,
                "closest_grocery_stores": groceries_data,
                "closest_clothing_stores": clothing_data,
            },
        }
        
        return feature
        
    except Exception as e:
        logger.error(f"[SIMPLIFIED_ERROR] Failed to create feature for listing {listing.id}: {str(e)}", exc_info=True)
        raise


def generate_simplified_geojson() -> Dict[str, Any]:
    """Generate the simplified GeoJSON payload as a Python dict."""
    request_start = time.time()

    if settings.DEBUG:
        reset_queries()

    logger.info("=" * 80)
    logger.info("[SIMPLIFIED_API_START] simplified_geojson build started")
    logger.info(
        f"[SIMPLIFIED_CONFIG] "
        f"Listings: {NUM_LISTINGS}, "
        f"Stations: {NUM_CLOSEST_STATIONS}, "
        f"Grocery: {NUM_CLOSEST_GROCERY_STORES}, "
        f"Clothing: {NUM_CLOSEST_CLOTHING_STORES}"
    )

    # Query limited listings
    query_start = time.time()
    listings = Listing.objects.all()[: NUM_LISTINGS]
    listings_list = list(listings)  # Force evaluation
    query_time = time.time() - query_start
    actual_count = len(listings_list)

    logger.info(
        f"[SIMPLIFIED_DB_QUERY] Retrieved {actual_count} listings | "
        f"Query time: {query_time:.4f}s"
    )

    # Process features
    features_start = time.time()
    features: List[Dict[str, Any]] = []

    for idx, listing in enumerate(listings_list, 1):
        try:
            feature = _simplified_listing_feature(listing)
            features.append(feature)
            logger.info(f"[SIMPLIFIED_PROGRESS] Processed listing {idx}/{actual_count}")

        except Exception as e:
            logger.error(f"[SIMPLIFIED_FEATURE_FAILED] Listing {listing.id} failed: {str(e)}")
            continue

    features_time = time.time() - features_start

    logger.info(
        f"[SIMPLIFIED_FEATURES_PROCESSED] Processed {len(features)} features | "
        f"Time: {features_time:.4f}s"
    )

    # Build response
    response_data = {
        "type": "FeatureCollection",
        "features": features,
        "config": {
            "num_listings": NUM_LISTINGS,
            "num_stations": NUM_CLOSEST_STATIONS,
            "num_grocery_stores": NUM_CLOSEST_GROCERY_STORES,
            "num_clothing_stores": NUM_CLOSEST_CLOTHING_STORES,
        }
    }

    total_time = time.time() - request_start

    logger.info(
        f"[SIMPLIFIED_API_COMPLETE] ✓ Success | "
        f"Total time: {total_time:.4f}s | "
        f"Features returned: {len(features)}"
    )
    logger.info("=" * 80)

    return response_data


def simplified_geojson(request: HttpRequest) -> JsonResponse:
    """
    Simplified endpoint that returns GeoJSON with limited listings and their closest items.
    Uses configurable constants at the top of this file to control output.
    """
    try:
        data = generate_simplified_geojson()
        return JsonResponse(data)
    except Exception as e:
        logger.error(
            f"[SIMPLIFIED_API_FAILED] ✗ Error: {str(e)}",
            exc_info=True
        )
        return JsonResponse({
            "type": "FeatureCollection",
            "features": [],
            "error": str(e)
        }, status=500)


# ============================================================================
# Nearby amenities API
# ============================================================================

COORD_REGEX = re.compile(r"(-?\d{1,3}(?:\.\d+)?)[ ,]+(-?\d{1,3}(?:\.\d+)?)")


def _coerce_positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default


def _extract_point_from_input(raw_input: str) -> Tuple[Point, str]:
    """
    Try to obtain a Point from various user inputs:
    - Google Maps link containing coordinates
    - Plain "lat, lon" coordinates
    - Free text address (geocoded via geopy)
    """
    cleaned = raw_input.strip()

    # Attempt to parse query params if present (common in Google Maps links)
    parsed_url = urlparse(cleaned)
    if parsed_url.query:
        query = parse_qs(parsed_url.query).get("q", [])
        if query:
            cleaned = query[0]

    match = COORD_REGEX.search(cleaned)
    if match:
        lat, lon = match.groups()
        return Point(float(lon), float(lat), srid=4326), "coordinates"

    # Fallback to geocoding
    try:
        from geopy.geocoders import Nominatim  # type: ignore
    except ImportError as exc:  # pragma: no cover - defensive
        raise ImportError("geopy is required for geocoding. Install via `pip install geopy`.") from exc

    geolocator = Nominatim(user_agent="proptech-geocoder")
    location = geolocator.geocode(cleaned, timeout=10)
    if not location:
        raise ValueError("Unable to geocode location input.")
    return Point(float(location.longitude), float(location.latitude), srid=4326), "geocoded"


def _serialize_nearby_queryset(model, point: Point, radius_m: float, max_results: int):
    """
    Annotate a queryset with distances and return serialized results.
    Handles both geography=True and geometry fields by transforming to WebMercator when needed.
    """
    field = model._meta.get_field("location")
    qs = model.objects.all()

    if getattr(field, "geography", False):
        qs = qs.annotate(distance=Distance("location", point))
    else:
        point_3857 = point.clone()
        point_3857.transform(3857)
        qs = qs.annotate(distance=Distance(Transform("location", 3857), point_3857))

    qs = qs.filter(distance__lte=radius_m).order_by("distance")[: max_results]

    serialized = []
    for obj in qs:
        distance_val = obj.distance
        try:
            distance_m = float(distance_val.m)  # type: ignore[attr-defined]
        except Exception:
            distance_m = float(distance_val)

        serialized.append(
            {
                "id": obj.id,
                "name": getattr(obj, "name", ""),
                "distance_m": distance_m,
                "lat": obj.location.y,
                "lng": obj.location.x,
            }
        )
    return serialized


@require_http_methods(["GET", "POST"])
def nearby_amenities(request: HttpRequest) -> JsonResponse:
    """
    Dynamic endpoint that accepts coordinates, Google Maps links, or free text,
    resolves them to a point, and returns nearby amenities within the configured radius.
    """
    raw_input = (
        request.GET.get("location")
        or request.GET.get("q")
        or request.POST.get("location")
    )

    # Try parsing JSON payload if no form/query param is present
    if not raw_input and request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            raw_input = payload.get("location") or payload.get("q")
        except (ValueError, AttributeError):
            pass

    if not raw_input:
        return JsonResponse({"error": "Provide a location via 'location' or 'q'."}, status=400)

    try:
        point, source = _extract_point_from_input(raw_input)
    except ImportError as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    config = NearbyAmenityConfig.get_config()
    radius_m = _coerce_positive_int(request.GET.get("radius_m"), config.radius_m)
    max_results = _coerce_positive_int(request.GET.get("max_results"), config.max_results)

    logger.info(
        f"[NEARBY_AMENITIES] input='{raw_input}' | source={source} | radius={radius_m}m | max={max_results}"
    )

    # Fetch raw data (single query for both LLM summary and map)
    raw_results: Dict[str, Any] = {}
    if config.enable_metro:
        raw_results["metro"] = _serialize_nearby_queryset(MetroStation, point, radius_m, max_results)
    if config.enable_metrobus:
        raw_results["metrobus"] = _serialize_nearby_queryset(MetrobusStation, point, radius_m, max_results)
    if config.enable_bus:
        raw_results["bus"] = _serialize_nearby_queryset(BusStop, point, radius_m, max_results)
    if config.enable_taxi:
        raw_results["taxi"] = _serialize_nearby_queryset(TaxiStand, point, radius_m, max_results)
    if config.enable_minibus:
        from tools.nearby_enrichment.minibus import nearby_minibus_segments
        minibus_data = nearby_minibus_segments(lon=point.x, lat=point.y, radius_m=radius_m, limit=max_results)
        raw_results["minibus"] = [{"id": item["id"], "name": item["name"], "geometry": item["geometry"]} for item in minibus_data]
    if config.enable_grocery:
        raw_results["grocery"] = _serialize_nearby_queryset(Grocery, point, radius_m, max_results)
    if config.enable_clothing:
        raw_results["clothing"] = _serialize_nearby_queryset(Clothing, point, radius_m, max_results)
    if config.enable_malls:
        raw_results["malls"] = _serialize_nearby_queryset(Mall, point, radius_m, max_results)
    if config.enable_parks:
        raw_results["parks"] = _serialize_nearby_queryset(Park, point, radius_m, max_results)
    if config.enable_schools:
        raw_results["schools"] = _serialize_nearby_queryset(School, point, radius_m, max_results)

    # Store raw results in session for map generation
    request.session["nearby_amenities_cache"] = {
        "query": raw_input,
        "point": {"lat": point.y, "lng": point.x},
        "radius_m": radius_m,
        "max_results": max_results,
        "raw_results": raw_results,
        "timestamp": time.time()
    }

    # Format LLM-friendly summary (take only first 5 for summary)
    llm_summary = {}
    
    # Metro stations (5 closest)
    if raw_results.get("metro"):
        stations = raw_results["metro"][:5]
        if stations:
            parts = ["{}, {}m".format(s["name"], round(s["distance_m"])) for s in stations]
            llm_summary["metro"] = "Metro (5): {}".format(", ".join(parts))
    
    # Metrobus stations (5 closest)
    if raw_results.get("metrobus"):
        stations = raw_results["metrobus"][:5]
        if stations:
            parts = ["{}, {}m".format(s["name"], round(s["distance_m"])) for s in stations]
            llm_summary["metrobus"] = "Metrobus (5): {}".format(", ".join(parts))
    
    # Bus stops (closest and farthest)
    if raw_results.get("bus"):
        stops = raw_results["bus"][:5]
        if stops:
            closest = stops[0]
            farthest = stops[-1]
            llm_summary["bus"] = "Bus stations (5): Closest: {}, {}m | Farthest: {}, {}m".format(
                closest['name'], round(closest['distance_m']),
                farthest['name'], round(farthest['distance_m'])
            )
    
    # Taxi stands (closest and farthest)
    if raw_results.get("taxi"):
        stands = raw_results["taxi"][:5]
        if stands:
            closest = stands[0]
            farthest = stands[-1]
            llm_summary["taxi"] = "Taxi stops: Closest: {}, {}m | Farthest: {}, {}m".format(
                closest['name'], round(closest['distance_m']),
                farthest['name'], round(farthest['distance_m'])
            )
    
    # Grocery stores (5 closest, name and distance only)
    if raw_results.get("grocery"):
        stores = raw_results["grocery"][:5]
        if stores:
            parts = ["{}, {}m".format(s["name"], round(s["distance_m"])) for s in stores]
            llm_summary["grocery"] = "Grocery (5): {}".format(", ".join(parts))
    
    # Clothing stores (5 closest, name and distance only)
    if raw_results.get("clothing"):
        stores = raw_results["clothing"][:5]
        if stores:
            parts = ["{}, {}m".format(s["name"], round(s["distance_m"])) for s in stores]
            llm_summary["clothing"] = "Clothing (5): {}".format(", ".join(parts))
    
    # Shopping malls (name and distance)
    if raw_results.get("malls"):
        malls = raw_results["malls"][:5]
        if malls:
            parts = ["{}, {}m".format(m["name"], round(m["distance_m"])) for m in malls]
            llm_summary["malls"] = "Malls: {}".format(", ".join(parts))
    
    # Parks (name and distance)
    if raw_results.get("parks"):
        parks = raw_results["parks"][:5]
        if parks:
            parts = ["{}, {}m".format(p["name"], round(p["distance_m"])) for p in parks]
            llm_summary["parks"] = "Parks: {}".format(", ".join(parts))
    
    # Schools (name and distance)
    if raw_results.get("schools"):
        schools = raw_results["schools"][:5]
        if schools:
            parts = ["{}, {}m".format(s["name"], round(s["distance_m"])) for s in schools]
            llm_summary["schools"] = "Schools: {}".format(", ".join(parts))

    # Build full URL for the map
    map_url = request.build_absolute_uri("/map/amenities/")
    
    response_data = {
        "query": raw_input,
        "location": {"lat": round(point.y, 6), "lng": round(point.x, 6)},
        "radius_m": radius_m,
        "summary": llm_summary,
        "interactive_map_url": map_url
    }

    return JsonResponse(response_data)


@require_http_methods(["GET"])
def nearby_amenities_map(request: HttpRequest) -> HttpResponse:
    """
    Generate a standalone shareable HTML map showing nearby amenities.
    Uses cached data from the API call to avoid re-querying the database.
    """
    # Try to get cached data from session
    cached_data = request.session.get("nearby_amenities_cache")
    
    if cached_data:
        # Check if cache is still fresh (within 5 minutes)
        cache_age = time.time() - cached_data.get("timestamp", 0)
        if cache_age < 300:  # 5 minutes
            logger.info("[NEARBY_MAP] Using cached data from API call")
            
            context = {
                "query": cached_data["query"],
                "center_lat": cached_data["point"]["lat"],
                "center_lng": cached_data["point"]["lng"],
                "radius_m": cached_data["radius_m"],
                "amenities_json": json.dumps(cached_data["raw_results"], ensure_ascii=False),
            }
            
            return render(request, "listings/nearby_amenities_map.html", context)
    
    # Fallback: if no cache or cache expired, query again
    raw_input = request.GET.get("location") or request.GET.get("q")
    
    if not raw_input:
        return HttpResponse("Provide a location via 'location' or 'q' parameter, or call the API first.", status=400)

    try:
        point, source = _extract_point_from_input(raw_input)
    except ImportError as exc:
        return HttpResponse(f"Error: {str(exc)}", status=500)
    except ValueError as exc:
        return HttpResponse(f"Error: {str(exc)}", status=400)

    config = NearbyAmenityConfig.get_config()
    radius_m = _coerce_positive_int(request.GET.get("radius_m"), config.radius_m)
    max_results = _coerce_positive_int(request.GET.get("max_results"), config.max_results)

    logger.info(
        f"[NEARBY_MAP] Cache miss - querying database | input='{raw_input}' | source={source} | radius={radius_m}m | max={max_results}"
    )

    # Collect all amenities
    amenities_data = {}
    if config.enable_metro:
        amenities_data["metro"] = _serialize_nearby_queryset(MetroStation, point, radius_m, max_results)
    if config.enable_metrobus:
        amenities_data["metrobus"] = _serialize_nearby_queryset(MetrobusStation, point, radius_m, max_results)
    if config.enable_bus:
        amenities_data["bus"] = _serialize_nearby_queryset(BusStop, point, radius_m, max_results)
    if config.enable_taxi:
        amenities_data["taxi"] = _serialize_nearby_queryset(TaxiStand, point, radius_m, max_results)
    if config.enable_minibus:
        from tools.nearby_enrichment.minibus import nearby_minibus_segments
        minibus_data = nearby_minibus_segments(lon=point.x, lat=point.y, radius_m=radius_m, limit=max_results)
        amenities_data["minibus"] = [{"id": item["id"], "name": item["name"], "geometry": item["geometry"]} for item in minibus_data]
    if config.enable_grocery:
        amenities_data["grocery"] = _serialize_nearby_queryset(Grocery, point, radius_m, max_results)
    if config.enable_clothing:
        amenities_data["clothing"] = _serialize_nearby_queryset(Clothing, point, radius_m, max_results)
    if config.enable_malls:
        amenities_data["malls"] = _serialize_nearby_queryset(Mall, point, radius_m, max_results)
    if config.enable_parks:
        amenities_data["parks"] = _serialize_nearby_queryset(Park, point, radius_m, max_results)
    if config.enable_schools:
        amenities_data["schools"] = _serialize_nearby_queryset(School, point, radius_m, max_results)

    context = {
        "query": raw_input,
        "center_lat": point.y,
        "center_lng": point.x,
        "radius_m": radius_m,
        "amenities_json": json.dumps(amenities_data, ensure_ascii=False),
    }

    return render(request, "listings/nearby_amenities_map.html", context)
