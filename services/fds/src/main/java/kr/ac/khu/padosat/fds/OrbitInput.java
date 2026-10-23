package kr.ac.khu.padosat.fds;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Properties;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.errors.OrekitException;
import org.orekit.propagation.analytical.tle.TLE;
import org.orekit.time.AbsoluteDate;
import org.orekit.time.TimeScalesFactory;
import org.orekit.utils.PVCoordinates;

/** Strict local body parser. N05/N11 transport authentication remains at the exchange boundary. */
public final class OrbitInput {
    public record GnssState(int noradId, Instant epoch, PVCoordinates pv) { }
    public record Selected(String source, String model, AbsoluteDate epoch, TLE tle,
                           GnssState gnss, String reason, Instant lastGnssEpoch) { }

    private OrbitInput() { }

    public static Selected select(Path tlePath, Path gnssPath, Instant start, int noradId)
            throws IOException {
        String fallbackReason = "GNSS_MISSING";
        Instant lastGnssEpoch = null;
        if (gnssPath != null && Files.exists(gnssPath)) {
            GnssState gnss = readGnss(gnssPath);
            if (gnss.noradId() != noradId) {
                throw new IllegalArgumentException("GNSS satellite ID mismatch");
            }
            long ageSeconds = Duration.between(gnss.epoch(), start).getSeconds();
            if (ageSeconds < 0) {
                throw new IllegalArgumentException("GNSS epoch is after planning start");
            }
            if (ageSeconds < 72L * 3600L) {
                return new Selected("GNSS", "NUMERICAL_GRAVITY_THIRD_BODY_PRELIMINARY",
                        date(gnss.epoch()), null, gnss, "GNSS_RECEIVED_WITHIN_72H",
                        gnss.epoch());
            }
            fallbackReason = "GNSS_STALE_72H";
            lastGnssEpoch = gnss.epoch();
        }
        if (tlePath == null) {
            throw new IllegalArgumentException("TLE required when GNSS is missing or >=72h old");
        }
        TLE tle = readTle(tlePath);
        if (tle.getSatelliteNumber() != noradId) {
            throw new IllegalArgumentException("TLE satellite ID mismatch");
        }
        AbsoluteDate startDate = date(start);
        double ageSeconds = startDate.durationFrom(tle.getDate());
        if (ageSeconds < 0 || ageSeconds >= 72.0 * 3600.0) {
            throw new IllegalArgumentException("TLE epoch outside allowed 72h age");
        }
        return new Selected("TLE", "SGP4", tle.getDate(), tle, null,
                fallbackReason, lastGnssEpoch);
    }

    public static TLE readTle(Path path) throws IOException {
        if (Files.size(path) > 256) {
            throw new IllegalArgumentException("TLE body too large");
        }
        List<String> lines = Files.readAllLines(path, StandardCharsets.US_ASCII);
        if (lines.size() != 2) {
            throw new IllegalArgumentException("invalid two-line TLE");
        }
        TLE tle;
        try {
            if (!TLE.isFormatOK(lines.get(0), lines.get(1))) {
                throw new IllegalArgumentException("invalid two-line TLE");
            }
            tle = new TLE(lines.get(0), lines.get(1));
        } catch (OrekitException error) {
            throw new IllegalArgumentException("invalid TLE content or checksum", error);
        }
        if (tle.getSatelliteNumber() <= 0) {
            throw new IllegalArgumentException("invalid TLE satellite number");
        }
        return tle;
    }

    public static GnssState readGnss(Path path) throws IOException {
        if (Files.size(path) > 4096) {
            throw new IllegalArgumentException("GNSS state body too large");
        }
        Properties p = strictProperties(path);
        if (!"fds.gnss-state.v1".equals(p.getProperty("schema")) ||
                !"ITRF".equals(p.getProperty("frame"))) {
            throw new IllegalArgumentException("GNSS schema or frame unsupported");
        }
        if (!p.stringPropertyNames().equals(java.util.Set.of("schema", "frame", "epoch_utc",
                "norad_id",
                "x_m", "y_m", "z_m", "vx_mps", "vy_mps", "vz_mps",
                "position_valid", "velocity_valid"))) {
            throw new IllegalArgumentException("GNSS state keys invalid");
        }
        if (!"true".equals(p.getProperty("position_valid")) ||
                !"true".equals(p.getProperty("velocity_valid"))) {
            throw new IllegalArgumentException("GNSS position and velocity must be valid");
        }
        String utc = p.getProperty("epoch_utc");
        if (!utc.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) {
            throw new IllegalArgumentException("GNSS epoch must be whole-second UTC Z");
        }
        Vector3D position = new Vector3D(number(p, "x_m"), number(p, "y_m"), number(p, "z_m"));
        Vector3D velocity = new Vector3D(number(p, "vx_mps"), number(p, "vy_mps"),
                number(p, "vz_mps"));
        if (position.getNorm() < 6.3e6 || position.getNorm() > 1.0e8 ||
                velocity.getNorm() < 100 || velocity.getNorm() > 15000) {
            throw new IllegalArgumentException("GNSS state outside bounded orbit envelope");
        }
        int id;
        try {
            id = Integer.parseInt(p.getProperty("norad_id"));
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException("invalid GNSS satellite ID", error);
        }
        if (id < 1 || id > 999999) {
            throw new IllegalArgumentException("invalid GNSS satellite ID");
        }
        return new GnssState(id, Instant.parse(utc), new PVCoordinates(position, velocity));
    }

    private static Properties strictProperties(Path path) throws IOException {
        Properties result = new Properties();
        for (String raw : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (raw.isBlank() || raw.startsWith("#")) {
                continue;
            }
            int separator = raw.indexOf('=');
            if (separator < 1 || raw.indexOf('=', separator + 1) >= 0) {
                throw new IllegalArgumentException("malformed GNSS state field");
            }
            String key = raw.substring(0, separator);
            String value = raw.substring(separator + 1);
            if (!key.matches("[a-z_]+") || value.isEmpty() ||
                    value.chars().anyMatch(character -> character < 33 || character > 126) ||
                    result.putIfAbsent(key, value) != null) {
                throw new IllegalArgumentException("invalid or repeated GNSS state field");
            }
        }
        return result;
    }

    private static double number(Properties p, String key) {
        if (p.getProperty(key) == null) {
            throw new IllegalArgumentException("missing GNSS component: " + key);
        }
        try {
            double value = Double.parseDouble(p.getProperty(key));
            if (Double.isFinite(value)) {
                return value;
            }
        } catch (NumberFormatException ignored) {
            // Reject malformed and non-finite values through the same path.
        }
        throw new IllegalArgumentException("invalid GNSS component: " + key);
    }

    public static AbsoluteDate date(Instant instant) {
        return new AbsoluteDate(java.util.Date.from(instant), TimeScalesFactory.getUTC());
    }
}
