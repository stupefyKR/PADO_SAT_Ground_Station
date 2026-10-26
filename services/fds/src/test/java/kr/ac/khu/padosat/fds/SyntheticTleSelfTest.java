package kr.ac.khu.padosat.fds;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.analytical.tle.TLE;
import org.orekit.propagation.analytical.tle.TLEPropagator;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.Constants;
import org.orekit.utils.IERSConventions;

/** Checks the test-only orbit geometry, wire syntax and refresh continuity. */
public final class SyntheticTleSelfTest {
    private SyntheticTleSelfTest() { }

    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 2) {
            throw new IllegalArgumentException("expected Orekit data and scratch directories");
        }
        OrbitModel.configureOfflineData(Path.of(arguments[0]));
        Path scratch = Files.createTempDirectory(Path.of(arguments[1]), "synthetic-tle-test-");
        try {
            TLE reference = SyntheticTleMain.reference();
            checkTle(reference);
            check(reference.getSatelliteNumber() == SyntheticTleMain.SATELLITE_NUMBER,
                    "synthetic satellite identity");
            check(Math.abs(Math.toDegrees(reference.getI()) - 97.4018) < 0.01,
                    "500 km J2 sun-synchronous inclination");
            check(Math.abs(reference.getMeanMotion() * 86_400.0 / (2 * Math.PI)
                    - 15.21936) < 0.01, "500 km mean motion");
            check(Math.abs(localTimeHours(reference.getRaan(),
                    SyntheticTleMain.REFERENCE_EPOCH) - 12.0) < 0.001,
                    "mean-solar LTAN at reference epoch");
            check(Math.abs(propagatedLocalTimeHours(reference,
                    SyntheticTleMain.REFERENCE_EPOCH) - 12.0) < 0.1,
                    "propagated ascending-node time near 12:00");
            check(Math.abs(propagatedLocalTimeHours(reference,
                    SyntheticTleMain.REFERENCE_EPOCH.plusSeconds(72L * 3600L)) - 12.0) < 0.1,
                    "sun-synchronous local time over 72 hours");
            // Independent check: find an actual south-to-north equator
            // crossing and use UTC + Earth-fixed longitude / 15 deg. This
            // does not reuse the generator's mean-Sun/RAAN helper.
            double crossingLtan = crossingLocalTimeHours(reference,
                    SyntheticTleMain.REFERENCE_EPOCH);
            double thirdDayLtan = crossingLocalTimeHours(reference,
                    SyntheticTleMain.REFERENCE_EPOCH.plusSeconds(72L * 3600L));
            check(Math.abs(crossingLtan - 12.0) < 0.1,
                    "actual ascending equator crossing near local noon");
            check(Math.abs(thirdDayLtan - 12.0) < 0.1,
                    "actual ascending crossing local time after 72 hours");
            System.out.printf(java.util.Locale.ROOT,
                    "Ascending crossing LTAN: %.6f h; after 72 h: %.6f h%n",
                    crossingLtan, thirdDayLtan);

            Instant after15Minutes = SyntheticTleMain.REFERENCE_EPOCH.plusSeconds(900);
            Instant after30Minutes = after15Minutes.plusSeconds(900);
            Instant after72Hours = SyntheticTleMain.REFERENCE_EPOCH.plusSeconds(72L * 3600L);
            TLE refreshed = SyntheticTleMain.at(after15Minutes);
            TLE later = SyntheticTleMain.at(after30Minutes);
            TLE thirdDay = SyntheticTleMain.at(after72Hours);
            checkTle(refreshed);
            checkTle(later);
            checkTle(thirdDay);
            check(Math.abs(refreshed.getDate().durationFrom(OrbitInput.date(after15Minutes)))
                    < 0.001, "fresh TLE epoch");
            checkPositionContinuity(reference, refreshed, after15Minutes);
            checkPositionContinuity(reference, later, after30Minutes);
            checkPositionContinuity(refreshed, later, after30Minutes);
            checkPositionContinuity(reference, thirdDay, after72Hours);

            var initialPosition = TLEPropagator.selectExtrapolator(reference)
                    .getPVCoordinates(reference.getDate(), FramesFactory.getTEME())
                    .getPosition();
            double sphericalAltitude = initialPosition.getNorm()
                    - Constants.WGS84_EARTH_EQUATORIAL_RADIUS;
            check(Math.abs(sphericalAltitude - SyntheticTleMain.ALTITUDE_M) < 15_000,
                    "SGP4 altitude near 500 km");

            Path output = scratch.resolve("test-only.tle");
            SyntheticTleMain.main(new String[] {"--orekit-data", arguments[0], "--epoch",
                    after15Minutes.toString(), "--out", output.toString()});
            check(Files.readAllLines(output, StandardCharsets.US_ASCII).size() == 2,
                    "two-line output");
            check(OrbitInput.readTle(output).getSatelliteNumber()
                    == SyntheticTleMain.SATELLITE_NUMBER, "FDS accepts synthetic TLE");
            System.out.println("Synthetic TLE tests passed");
        } finally {
            Files.deleteIfExists(scratch.resolve("test-only.tle"));
            Files.deleteIfExists(scratch);
        }
    }

    private static void checkTle(TLE tle) {
        check(tle.getLine1().length() == 69 && tle.getLine2().length() == 69,
                "TLE 69-character lines");
        check(TLE.isFormatOK(tle.getLine1(), tle.getLine2()), "TLE format and checksums");
        check(tle.getSatelliteNumber() == SyntheticTleMain.SATELLITE_NUMBER,
                "synthetic satellite number retained");
    }

    private static void checkPositionContinuity(TLE before, TLE after, Instant epoch) {
        AbsoluteDate date = OrbitInput.date(epoch);
        var frame = FramesFactory.getTEME();
        var a = TLEPropagator.selectExtrapolator(before).getPVCoordinates(date, frame);
        var b = TLEPropagator.selectExtrapolator(after).getPVCoordinates(date, frame);
        check(Vector3D.distance(a.getPosition(), b.getPosition()) < 1000.0,
                "TLE refresh must not teleport the satellite");
        check(Vector3D.distance(a.getVelocity(), b.getVelocity()) < 2.0,
                "TLE refresh velocity continuity");
    }

    private static double localTimeHours(double raan, Instant epoch) {
        double angle = Math.IEEEremainder(raan
                - SyntheticTleMain.meanSunRightAscension(epoch), 2.0 * Math.PI);
        return (12.0 + angle * 12.0 / Math.PI + 24.0) % 24.0;
    }

    private static double propagatedLocalTimeHours(TLE tle, Instant epoch) {
        var pv = TLEPropagator.selectExtrapolator(tle)
                .getPVCoordinates(OrbitInput.date(epoch), FramesFactory.getTEME());
        Vector3D angularMomentum = Vector3D.crossProduct(pv.getPosition(), pv.getVelocity());
        Vector3D ascendingNode = Vector3D.crossProduct(Vector3D.PLUS_K, angularMomentum);
        return localTimeHours(Math.atan2(ascendingNode.getY(), ascendingNode.getX()), epoch);
    }

    private static double crossingLocalTimeHours(TLE tle, Instant epoch) {
        var propagator = TLEPropagator.selectExtrapolator(tle);
        var itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, true);
        AbsoluteDate start = OrbitInput.date(epoch);
        double before = 0.0;
        double previousZ = propagator.getPVCoordinates(start, itrf).getPosition().getZ();
        for (double after = 60.0; after <= 3.0 * 3600.0; after += 60.0) {
            double nextZ = propagator.getPVCoordinates(start.shiftedBy(after), itrf)
                    .getPosition().getZ();
            if (previousZ <= 0.0 && nextZ > 0.0) {
                double low = before, high = after;
                while (high - low > 0.001) {
                    double middle = (low + high) / 2.0;
                    double z = propagator.getPVCoordinates(start.shiftedBy(middle), itrf)
                            .getPosition().getZ();
                    if (z > 0.0) high = middle;
                    else low = middle;
                }
                double crossing = (low + high) / 2.0;
                Vector3D position = propagator.getPVCoordinates(start.shiftedBy(crossing), itrf)
                        .getPosition();
                double utcHours = ((epoch.getEpochSecond() + crossing) % 86400.0) / 3600.0;
                double longitudeHours = Math.atan2(position.getY(), position.getX())
                        * 12.0 / Math.PI;
                return (utcHours + longitudeHours + 24.0) % 24.0;
            }
            before = after;
            previousZ = nextZ;
        }
        throw new AssertionError("ascending equator crossing not found");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
