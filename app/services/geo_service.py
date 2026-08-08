"""
SecureTrack Platform — Geo Service
Haversine distance calculations and geofence validation.
"""
import math
from typing import List, Tuple


class GeoService:
    """Haversine distance calculations and geofence validation."""

    EARTH_RADIUS_KM = 6371.0

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the great-circle distance between two points on Earth
        using the Haversine formula.

        Returns:
            Distance in kilometers.
        """
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return GeoService.EARTH_RADIUS_KM * c

    @staticmethod
    def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate distance in meters between two GPS coordinates.

        Returns:
            Distance in meters.
        """
        return GeoService.haversine_distance(lat1, lon1, lat2, lon2) * 1000.0

    @staticmethod
    def is_within_geofence(
        site_lat: float,
        site_lng: float,
        check_lat: float,
        check_lng: float,
        radius_meters: float,
    ) -> Tuple[bool, float]:
        """
        Check if a GPS coordinate is within a site's geofence radius.

        Args:
            site_lat: Site center latitude
            site_lng: Site center longitude
            check_lat: Coordinate to check (supervisor's GPS)
            check_lng: Coordinate to check (supervisor's GPS)
            radius_meters: Geofence radius in meters

        Returns:
            Tuple of (is_within: bool, distance_meters: float)
        """
        distance_m = GeoService.haversine_distance_meters(
            site_lat, site_lng, check_lat, check_lng
        )
        return (distance_m <= radius_meters, round(distance_m, 1))

    @staticmethod
    def filter_by_proximity(
        items: list,
        center_lat: float,
        center_lng: float,
        radius_km: float = 50.0,
        lat_attr: str = "latitude",
        lng_attr: str = "longitude",
    ) -> List[Tuple]:
        """
        Filter and sort items by proximity to a center point.

        Args:
            items: List of objects with lat/lng attributes
            center_lat: Center latitude
            center_lng: Center longitude
            radius_km: Maximum distance in km (default 50)
            lat_attr: Name of latitude attribute on items
            lng_attr: Name of longitude attribute on items

        Returns:
            List of (item, distance_km) tuples sorted by distance.
        """
        results = []

        for item in items:
            item_lat = getattr(item, lat_attr, None)
            item_lng = getattr(item, lng_attr, None)

            if item_lat is None or item_lng is None:
                continue

            distance = GeoService.haversine_distance(
                center_lat, center_lng, item_lat, item_lng
            )

            if distance <= radius_km:
                results.append((item, round(distance, 2)))

        # Sort by distance (nearest first)
        results.sort(key=lambda x: x[1])
        return results
