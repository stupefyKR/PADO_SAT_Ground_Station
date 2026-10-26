package kr.ac.khu.padosat.fds;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.analytical.tle.TLE;
import org.orekit.propagation.analytical.tle.TLEPropagator;

/** Check that the fictional ISS display refit remains an SGP4-continuous test input. */
public final class IssDisplayTleSelfTest {
    private IssDisplayTleSelfTest() { }

    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 3) {
            throw new IllegalArgumentException("expected Orekit data, ISS fixture, and scratch directories");
        }
        OrbitModel.configureOfflineData(Path.of(arguments[0]));
        TLE reference = IssDisplayTleMain.readReference(Path.of(arguments[1]));
        Path scratch = Files.createTempDirectory(Path.of(arguments[2]), "iss-display-test-");
        Path generated = scratch.resolve("fictional-iss.tle");
        try {
            check(reference.getSatelliteNumber() == 25544, "fixed ISS reference ID");
            Instant epoch = Instant.parse("2026-10-26T00:00:00Z");
            TLE refreshed = IssDisplayTleMain.at(reference, epoch);
            check(TLE.isFormatOK(refreshed.getLine1(), refreshed.getLine2()),
                    "refreshed TLE syntax and checksums");
            check(refreshed.getSatelliteNumber() == 25544, "refreshed ISS ID");
            check(!refreshed.getLine1().equals(reference.getLine1()),
                    "old reference epoch must not be passed through");
            check(Math.abs(refreshed.getDate().durationFrom(OrbitInput.date(epoch))) < 0.001,
                    "fresh epoch for strict FDS input gate");
            checkContinuity(reference, refreshed, epoch);
            Instant later = epoch.plusSeconds(72L * 3600L);
            TLE next = IssDisplayTleMain.at(reference, later);
            checkContinuity(reference, next, later);
            checkContinuity(refreshed, next, later);
            IssDisplayTleMain.main(new String[] {"--orekit-data", arguments[0],
                    "--reference", arguments[1], "--epoch", epoch.toString(),
                    "--out", generated.toString()});
            check(OrbitInput.select(generated, null, epoch.plusSeconds(2), 25544)
                    .source().equals("TLE"), "refreshed test TLE passes normal FDS gate");
            System.out.println("ISS display TLE tests passed");
        } finally {
            Files.deleteIfExists(generated);
            Files.deleteIfExists(scratch);
        }
    }

    private static void checkContinuity(TLE before, TLE after, Instant epoch) {
        var date = OrbitInput.date(epoch);
        var frame = FramesFactory.getTEME();
        var first = TLEPropagator.selectExtrapolator(before).getPVCoordinates(date, frame);
        var second = TLEPropagator.selectExtrapolator(after).getPVCoordinates(date, frame);
        check(Vector3D.distance(first.getPosition(), second.getPosition()) < 1000.0,
                "refit position continuity at display epoch");
        check(Vector3D.distance(first.getVelocity(), second.getVelocity()) < 2.0,
                "refit velocity continuity at display epoch");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new IllegalStateException(message);
        }
    }
}
