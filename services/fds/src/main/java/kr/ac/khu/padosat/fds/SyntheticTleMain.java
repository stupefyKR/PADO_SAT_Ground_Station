package kr.ac.khu.padosat.fds;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.Map;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.analytical.tle.TLE;
import org.orekit.propagation.analytical.tle.TLEPropagator;
import org.orekit.propagation.analytical.tle.generation.FixedPointTleGenerationAlgorithm;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.Constants;
import org.orekit.utils.IERSConventions;

/**
 * TEST_ONLY fictitious PADO-SAT orbit, never an observed N05 TLE.
 *
 * <p>The reference is a near-circular 500 km J2 sun-synchronous orbit with
 * nominal 12:00 mean local time of the ascending node. Every refresh is fitted
 * to the same propagated reference state; changing the TLE epoch does not reset
 * the orbital phase. The J2 design is a first-order approximation, not a
 * precision orbit or a replacement for observed elements.</p>
 */
public final class SyntheticTleMain {
    public static final int SATELLITE_NUMBER = 99999;
    public static final double ALTITUDE_M = 500_000.0;
    public static final double TARGET_LTAN_HOURS = 12.0;
    public static final Instant REFERENCE_EPOCH = Instant.parse("2026-10-23T00:00:00Z");

    private static final double J2 = 1.08262668e-3;
    private static final double TROPICAL_YEAR_DAYS = 365.2422;
    private static final double SECONDS_PER_DAY = 86_400.0;
    private static final double TWO_PI = 2.0 * Math.PI;

    private SyntheticTleMain() { }

    public static void main(String[] arguments) throws IOException {
        Map<String, String> options = parseOptions(arguments);
        OrbitModel.configureOfflineData(Path.of(options.get("--orekit-data")));
        Instant epoch = parseEpoch(options.get("--epoch"));
        TLE tle = at(epoch);
        write(Path.of(options.get("--out")), tle);
        System.out.println("TEST_ONLY synthetic TLE for satellite " + SATELLITE_NUMBER
                + " at " + epoch + ": " + options.get("--out"));
    }

    /** Generate a new TLE epoch from one immutable synthetic reference orbit. */
    public static TLE at(Instant epoch) {
        AbsoluteDate date = OrbitInput.date(epoch);
        TLE reference = reference();
        if (epoch.equals(REFERENCE_EPOCH)) {
            return reference;
        }
        var state = TLEPropagator.selectExtrapolator(reference).propagate(date);
        return new FixedPointTleGenerationAlgorithm().generate(state, reference);
    }

    /** Initial mean elements. They describe a simulation and have no flight provenance. */
    public static TLE reference() {
        AbsoluteDate date = OrbitInput.date(REFERENCE_EPOCH);
        double radius = Constants.WGS84_EARTH_EQUATORIAL_RADIUS;
        double semiMajorAxis = radius + ALTITUDE_M;
        double meanMotion = Math.sqrt(Constants.WGS84_EARTH_MU
                / (semiMajorAxis * semiMajorAxis * semiMajorAxis));
        double sunRate = TWO_PI / (TROPICAL_YEAR_DAYS * SECONDS_PER_DAY);
        double cosineInclination = -sunRate / (1.5 * J2 * meanMotion
                * Math.pow(radius / semiMajorAxis, 2));
        double inclination = Math.acos(cosineInclination);
        double meanSunRightAscension = meanSunRightAscension(REFERENCE_EPOCH);
        double rightAscensionOfAscendingNode = Math.IEEEremainder(
                meanSunRightAscension + (TARGET_LTAN_HOURS - 12.0) * TWO_PI / 24.0,
                TWO_PI);

        // Small nonzero eccentricity avoids an undefined argument of perigee.
        return new TLE(SATELLITE_NUMBER, 'U', 2026, 1, "A", TLE.DEFAULT, 1,
                date, meanMotion, 0.0, 0.0, 0.0001, inclination, 0.0,
                rightAscensionOfAscendingNode, 0.0, 1, 0.0);
    }

    /**
     * Right ascension of the fictitious mean Sun in the TLE TEME frame.
     * Greenwich is at mean noon at 12:00 UTC; this deliberately uses mean
     * solar time rather than the apparent Sun, so the equation of time does
     * not shift the requested LTAN by several minutes.
     */
    public static double meanSunRightAscension(Instant epoch) {
        AbsoluteDate date = OrbitInput.date(epoch);
        Vector3D greenwich = FramesFactory.getITRF(IERSConventions.IERS_2010, true)
                .getTransformTo(FramesFactory.getTEME(), date)
                .transformVector(Vector3D.PLUS_I);
        double greenwichRightAscension = Math.atan2(greenwich.getY(), greenwich.getX());
        int utcSecondOfDay = LocalDateTime.ofInstant(epoch, ZoneOffset.UTC).toLocalTime()
                .toSecondOfDay();
        return Math.IEEEremainder(greenwichRightAscension + Math.PI
                - TWO_PI * utcSecondOfDay / SECONDS_PER_DAY, TWO_PI);
    }

    private static Instant parseEpoch(String text) {
        if (!text.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) {
            throw new IllegalArgumentException("--epoch must be whole-second UTC, e.g. 2026-10-23T00:00:00Z");
        }
        return Instant.parse(text);
    }

    private static Map<String, String> parseOptions(String[] arguments) {
        if (arguments.length != 6) {
            throw new IllegalArgumentException("usage: --orekit-data DIR --epoch UTC_ISO --out FILE");
        }
        Map<String, String> options = new HashMap<>();
        for (int i = 0; i < arguments.length; i += 2) {
            String name = arguments[i];
            if (!(name.equals("--orekit-data") || name.equals("--epoch") || name.equals("--out"))
                    || arguments[i + 1].isBlank() || options.putIfAbsent(name, arguments[i + 1]) != null) {
                throw new IllegalArgumentException("invalid synthetic TLE argument: " + name);
            }
        }
        return options;
    }

    private static void write(Path destination, TLE tle) throws IOException {
        Path absolute = destination.toAbsolutePath();
        Path directory = absolute.getParent();
        Files.createDirectories(directory);
        Path temporary = Files.createTempFile(directory, "synthetic-tle-", ".tmp");
        try {
            Files.writeString(temporary, tle.getLine1() + "\n" + tle.getLine2() + "\n",
                    StandardCharsets.US_ASCII);
            try {
                Files.move(temporary, absolute, StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException error) {
                Files.move(temporary, absolute, StandardCopyOption.REPLACE_EXISTING);
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }
}
