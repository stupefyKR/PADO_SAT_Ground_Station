package kr.ac.khu.padosat.fds;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

/** Validated calculation inputs. No antenna coordinate or RF value is compiled into the service. */
public record FdsConfig(int noradId, Instant start, int horizonHours, int ephemerisStepSeconds,
                        int profileStepSeconds, List<Station> stations, int gravityDegree,
                        int gravityOrder, double spacecraftMassKg) {
    public record Station(String id, String band, double latitudeDeg, double longitudeDeg,
                          double altitudeM, double minimumElevationDeg, double carrierHz,
                          boolean exampleFrequency) { }

    public static FdsConfig read(Path path) throws IOException {
        if (Files.size(path) > 8192) {
            throw new IllegalArgumentException("FDS configuration too large");
        }
        Properties p = new Properties();
        try (Reader reader = Files.newBufferedReader(path)) {
            p.load(reader);
        }
        requireKeys(p, List.of("expected_norad_id", "start_utc", "horizon_hours", "ephemeris_step_s",
                "profile_step_s", "gravity_degree", "gravity_order", "spacecraft_mass_kg",
                "station.yonsei.band", "station.yonsei.latitude_deg", "station.yonsei.longitude_deg",
                "station.yonsei.altitude_m", "station.yonsei.minimum_elevation_deg",
                "station.yonsei.carrier_hz", "station.yonsei.frequency_example",
                "station.kari.band", "station.kari.latitude_deg", "station.kari.longitude_deg",
                "station.kari.altitude_m", "station.kari.minimum_elevation_deg",
                "station.kari.carrier_hz", "station.kari.frequency_example"));
        String utc = p.getProperty("start_utc");
        if (!utc.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) {
            throw new IllegalArgumentException("start_utc must be whole-second UTC Z");
        }
        int hours = integer(p, "horizon_hours", 1, 72);
        int ephemerisStep = integer(p, "ephemeris_step_s", 1, 300);
        int profileStep = integer(p, "profile_step_s", 1, 60);
        int degree = integer(p, "gravity_degree", 2, 100);
        int order = integer(p, "gravity_order", 0, degree);
        double mass = number(p, "spacecraft_mass_kg", 0.01, 10000);
        return new FdsConfig(integer(p, "expected_norad_id", 1, 999999), Instant.parse(utc),
                hours, ephemerisStep, profileStep,
                List.of(station(p, "yonsei"), station(p, "kari")), degree, order, mass);
    }

    private static Station station(Properties p, String id) {
        String prefix = "station." + id + ".";
        String band = p.getProperty(prefix + "band");
        if (!(id.equals("yonsei") && band.equals("UHF")) &&
                !(id.equals("kari") && band.equals("S_BAND"))) {
            throw new IllegalArgumentException("unexpected band for " + id);
        }
        double latitude = number(p, prefix + "latitude_deg", -90, 90);
        double longitude = number(p, prefix + "longitude_deg", -180, 180);
        double altitude = number(p, prefix + "altitude_m", -500, 10000);
        double elevation = number(p, prefix + "minimum_elevation_deg", 0, 90);
        double frequency = number(p, prefix + "carrier_hz", 1.0e6, 20.0e9);
        String example = p.getProperty(prefix + "frequency_example");
        if (!example.equals("true") && !example.equals("false")) {
            throw new IllegalArgumentException("frequency_example must be true or false");
        }
        return new Station(id, band, latitude, longitude, altitude, elevation,
                frequency, Boolean.parseBoolean(example));
    }

    private static void requireKeys(Properties p, List<String> keys) {
        for (String key : keys) {
            if (p.getProperty(key) == null || p.getProperty(key).isBlank()) {
                throw new IllegalArgumentException("missing configuration: " + key);
            }
        }
        List<String> unknown = new ArrayList<>(p.stringPropertyNames());
        unknown.removeAll(keys);
        if (!unknown.isEmpty()) {
            throw new IllegalArgumentException("unknown configuration: " + unknown);
        }
    }

    private static int integer(Properties p, String key, int minimum, int maximum) {
        try {
            int value = Integer.parseInt(p.getProperty(key));
            if (value >= minimum && value <= maximum) {
                return value;
            }
        } catch (NumberFormatException ignored) {
            // The same bounded error is returned for malformed and out-of-range values.
        }
        throw new IllegalArgumentException(key + " outside allowed range");
    }

    private static double number(Properties p, String key, double minimum, double maximum) {
        try {
            double value = Double.parseDouble(p.getProperty(key));
            if (Double.isFinite(value) && value >= minimum && value <= maximum) {
                return value;
            }
        } catch (NumberFormatException ignored) {
            // The same bounded error is returned for malformed and out-of-range values.
        }
        throw new IllegalArgumentException(key + " outside allowed range");
    }
}
