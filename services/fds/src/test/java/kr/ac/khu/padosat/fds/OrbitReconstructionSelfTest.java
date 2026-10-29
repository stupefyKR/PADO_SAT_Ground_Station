package kr.ac.khu.padosat.fds;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.regex.Pattern;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.bodies.CelestialBodyFactory;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.analytical.tle.TLEPropagator;
import org.orekit.utils.IERSConventions;

/** Check the hindsight product against direct SGP4 and Sun frame calculations. */
public final class OrbitReconstructionSelfTest {
    private OrbitReconstructionSelfTest() { }

    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 2) {
            throw new IllegalArgumentException("expected Orekit data and scratch directories");
        }
        OrbitModel.configureOfflineData(Path.of(arguments[0]));
        Instant end = Instant.parse("2026-10-29T08:45:37Z");
        var tle = SyntheticTleMain.at(end.minusSeconds(2));
        var propagator = TLEPropagator.selectExtrapolator(tle);
        var frame = FramesFactory.getITRF(IERSConventions.IERS_2010, true);
        Path scratch = Files.createTempDirectory(Path.of(arguments[1]), "orbit-reconstruction-test-");
        Path output = scratch.resolve("orbit_reconstruction.json");
        try {
            OrbitReconstructionMain.write(output, tle, end, end);
            String json = Files.readString(output);
            check(json.contains("\"quality\":\"RECONSTRUCTED_NOT_MEASURED\""), "explicit hindsight quality");
            check(json.contains("\"start_utc\":\"2026-10-26T08:45:37Z\""), "full previous 72-hour interval");
            check(json.contains("\"source\":\"TLE\""), "no GNSS measurement claim");
            check(Files.size(output) < OrbitReconstructionMain.MAX_BYTES, "bounded JSON size");
            var rows = Pattern.compile("\\[\"([^\"]+)\",([^\\[\\]]+)\\]").matcher(json);
            int count = 0;
            while (rows.find()) {
                Instant instant = Instant.parse(rows.group(1));
                check(instant.equals(end.minusSeconds(OrbitReconstructionMain.WINDOW_SECONDS)
                        .plusSeconds((long) count * OrbitReconstructionMain.STEP_SECONDS)), "60-second ordered grid");
                String[] values = rows.group(2).split(",");
                check(values.length == 6, "position plus Sun vector only");
                Vector3D position = new Vector3D(Double.parseDouble(values[0]),
                        Double.parseDouble(values[1]), Double.parseDouble(values[2]));
                Vector3D sun = new Vector3D(Double.parseDouble(values[3]),
                        Double.parseDouble(values[4]), Double.parseDouble(values[5]));
                var date = OrbitInput.date(instant);
                check(Vector3D.distance(position, propagator.getPVCoordinates(date, frame).getPosition()) < .002,
                        "same synthetic TLE backward/forward state");
                check(Vector3D.distance(sun, CelestialBodyFactory.getSun().getPosition(date, frame).normalize()) < 2e-9,
                        "same ITRF Sun direction at historical time");
                count++;
            }
            check(count == 4321, "both endpoints and 4321 samples");
            check(tle.getDate().durationFrom(OrbitInput.date(end.minusSeconds(OrbitReconstructionMain.WINDOW_SECONDS))) > 259000,
                    "historical timestamps intentionally precede TLE epoch");
            System.out.println("Orbit reconstruction tests passed");
        } finally {
            Files.deleteIfExists(output);
            Files.deleteIfExists(scratch);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
