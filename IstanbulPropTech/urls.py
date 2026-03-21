from django.contrib import admin
from django.urls import path
from django_distill import distill_path
from django.conf import settings
from django.conf.urls.static import static
from listings.views import (
    map_view,
    listings_geojson,
    simplified_map_view,
    simplified_geojson,
    nearby_amenities,
    nearby_amenities_map,
    amenities_front,
)
from transit_layer.views import metro_stations_geojson, transit_geojson
from stores_layer.views import stores_geojson
from newflow.views import file_map_view, file_map_configure_view

urlpatterns = [
    path("admin/", admin.site.urls),
    distill_path("", map_view, name="map", distill_file="index.html"),
    distill_path("simplified/", simplified_map_view, name="simplified_map", distill_file="simplified/index.html"),
    distill_path("api/listings.geojson", listings_geojson, name="listings_geojson", distill_file="api/listings.geojson"),
    distill_path("api/listings-simplified.geojson", simplified_geojson, name="simplified_geojson", distill_file="api/listings-simplified.geojson"),
    distill_path("api/metro_stations.geojson", metro_stations_geojson, name="metro_stations_geojson", distill_file="api/metro_stations.geojson"),
    distill_path("api/transit.geojson", transit_geojson, name="transit_geojson", distill_file="api/transit.geojson"),
    distill_path("api/stores.geojson", stores_geojson, name="stores_geojson", distill_file="api/stores.geojson"),
    path("amenities/", amenities_front, name="amenities_front"),
    path("api/amenities/nearby/", nearby_amenities, name="nearby_amenities"),
    path("map/amenities/", nearby_amenities_map, name="nearby_amenities_map"),
    path("map/file/", file_map_view, name="file_map"),
    path("map/file/configure/", file_map_configure_view, name="file_map_configure"),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
