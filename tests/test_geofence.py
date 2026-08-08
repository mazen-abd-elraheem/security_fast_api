"""
SecureTrack — Geofence Math Tests
Pure unit tests for Haversine distance and geofence validation.
"""
import pytest
from app.services.geo_service import GeoService


class TestHaversineDistance:
    def test_zero_distance(self):
        d = GeoService.haversine_distance_meters(30.0444, 31.2357, 30.0444, 31.2357)
        assert d == 0.0

    def test_known_distance_cairo(self):
        """Cairo Tower to Egyptian Museum is ~1.2 km."""
        d = GeoService.haversine_distance(30.0459, 31.2243, 30.0478, 31.2336)
        assert 0.5 < d < 2.0  # ~1 km range

    def test_very_close_points(self):
        """Two points 50m apart."""
        d = GeoService.haversine_distance_meters(30.0444, 31.2357, 30.0448, 31.2357)
        assert d < 100  # Should be ~44m

    def test_cross_hemisphere(self):
        """Distance from Cairo to London ~3500 km."""
        d = GeoService.haversine_distance(30.0444, 31.2357, 51.5074, -0.1278)
        assert 3400 < d < 3600


class TestGeofenceValidation:
    def test_inside_geofence(self):
        """Point 50m from site center, radius 100m → inside."""
        is_within, distance = GeoService.is_within_geofence(
            30.0444, 31.2357,  # Site center
            30.0448, 31.2357,  # ~44m north
            100,               # 100m radius
        )
        assert is_within is True
        assert distance < 100

    def test_outside_geofence(self):
        """Point 500m from site center, radius 100m → outside."""
        is_within, distance = GeoService.is_within_geofence(
            30.0444, 31.2357,  # Site center
            30.0490, 31.2357,  # ~511m north
            100,               # 100m radius
        )
        assert is_within is False
        assert distance > 100

    def test_exactly_on_boundary(self):
        """Point at approximately the boundary radius."""
        # 100m north of center at this latitude ≈ 0.0009 degrees
        is_within, distance = GeoService.is_within_geofence(
            30.0444, 31.2357,
            30.0453, 31.2357,  # ~100m north
            100,
        )
        # Should be approximately at the boundary
        assert 80 < distance < 120

    def test_large_radius_geofence(self):
        """5000m radius captures a far point."""
        is_within, distance = GeoService.is_within_geofence(
            30.0444, 31.2357,
            30.0500, 31.2400,  # ~700m away
            5000,
        )
        assert is_within is True

    def test_very_small_radius(self):
        """10m radius — only exact location passes."""
        is_within, distance = GeoService.is_within_geofence(
            30.0444, 31.2357,
            30.0445, 31.2358,  # ~14m away
            10,
        )
        assert is_within is False


class TestProximityFilter:
    def test_filter_empty_list(self):
        result = GeoService.filter_by_proximity([], 30.0, 31.0, 50.0)
        assert result == []

    def test_filter_sorts_by_distance(self):
        class MockItem:
            def __init__(self, lat, lng):
                self.latitude = lat
                self.longitude = lng

        items = [
            MockItem(30.1, 31.3),  # Far
            MockItem(30.05, 31.24),  # Close
            MockItem(30.0444, 31.2357),  # Very close
        ]
        result = GeoService.filter_by_proximity(items, 30.0444, 31.2357, 50.0)
        assert len(result) == 3
        # Should be sorted nearest first
        assert result[0][1] < result[1][1] < result[2][1]
