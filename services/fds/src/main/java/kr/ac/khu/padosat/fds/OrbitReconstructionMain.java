package kr.ac.khu.padosat.fds;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.bodies.CelestialBodyFactory;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.analytical.tle.TLE;
import org.orekit.propagation.analytical.tle.TLEPropagator;
import org.orekit.utils.IERSConventions;

/**
 * Display-only reconstruction of the preceding 72 hours from a synthetic TLE.
 *
 * <p>These are retrospective SGP4 model positions, never measured GNSS or the
 * predictions originally available at those times. This product is separate
 * from the normal FDS prediction, PASS schedule, and satellite truth view.</p>
 */
public final class OrbitReconstructionMain {
    public static final long WINDOW_SECONDS = 72L * 3600L;
    public static final int STEP_SECONDS = 60;
    public static final int MAX_BYTES = 2 * 1024 * 1024;
    public static final int SATELLITE_NUMBER = 99999;

    private OrbitReconstructionMain() { }

    public static void main(String[] arguments) throws IOException {
        Map<String, String> options = parseOptions(arguments);
        OrbitModel.configureOfflineData(Path.of(options.get("--orekit-data")));
        TLE tle = OrbitInput.readTle(Path.of(options.get("--tle")));
        Instant end = parseUtc(options.get("--end"));
        write(Path.of(options.get("--out")), tle, end,
                Instant.now().truncatedTo(ChronoUnit.SECONDS));
        System.out.println("Synthetic TLE reconstruction through " + end + ": " + options.get("--out"));
    }

    static void write(Path destination, TLE tle, Instant end, Instant generated) throws IOException {
        if (tle.getSatelliteNumber() != SATELLITE_NUMBER || end.getNano() != 0
                || generated.getNano() != 0 || generated.isBefore(end)) {
            throw new IllegalArgumentException("whole-second synthetic reconstruction required");
        }
        double age = OrbitInput.date(end).durationFrom(tle.getDate());
        if (age < 0.0 || age >= WINDOW_SECONDS) {
            throw new IllegalArgumentException("reconstruction requires a TLE younger than 72 hours at end");
        }
        TLEPropagator orbit = TLEPropagator.selectExtrapolator(tle);
        if (!"SGP4".equals(orbit.getClass().getSimpleName())) {
            throw new IllegalArgumentException("synthetic reconstruction requires SGP4");
        }
        var itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, true);
        Instant start = end.minusSeconds(WINDOW_SECONDS);
        Path absolute = destination.toAbsolutePath();
        Files.createDirectories(absolute.getParent());
        Path temporary = Files.createTempFile(absolute.getParent(), ".orbit-reconstruction-", ".tmp");
        try {
            try (BufferedWriter writer = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8)) {
                writer.write("{\"schema\":\"pado.orbit-reconstruction.v1\","
                        + "\"quality\":\"RECONSTRUCTED_NOT_MEASURED\",\"norad_id\":99999,\"source\":\"TLE\"");
                writer.write(String.format(Locale.ROOT,
                        ",\"input_epoch_utc\":\"%s\",\"generated_utc\":\"%s\","
                                + "\"start_utc\":\"%s\",\"end_utc\":\"%s\",\"sample_step_s\":60,\"samples\":[",
                        FdsOutput.utc(tle.getDate()), generated, start, end));
                boolean first = true;
                for (long offset = 0; offset <= WINDOW_SECONDS; offset += STEP_SECONDS) {
                    if (!first) writer.write(',');
                    first = false;
                    Instant instant = start.plusSeconds(offset);
                    var date = OrbitInput.date(instant);
                    Vector3D position = orbit.getPVCoordinates(date, itrf).getPosition();
                    Vector3D sun = CelestialBodyFactory.getSun().getPosition(date, itrf).normalize();
                    if (!Double.isFinite(position.getNorm()) || position.getNorm() < 6.3e6
                            || position.getNorm() > 8e6 || !Double.isFinite(sun.getNorm())) {
                        throw new IllegalArgumentException("reconstructed orbit outside display envelope");
                    }
                    writer.write(String.format(Locale.ROOT,
                            "[\"%s\",%.3f,%.3f,%.3f,%.9f,%.9f,%.9f]", instant,
                            position.getX(), position.getY(), position.getZ(),
                            sun.getX(), sun.getY(), sun.getZ()));
                }
                writer.write("]}\n");
            }
            if (Files.size(temporary) > MAX_BYTES) {
                throw new IllegalArgumentException("reconstruction exceeds 2 MiB display limit");
            }
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

    private static Instant parseUtc(String text) {
        if (!text.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) {
            throw new IllegalArgumentException("--end must be whole-second UTC");
        }
        return Instant.parse(text);
    }

    private static Map<String, String> parseOptions(String[] arguments) {
        if (arguments.length != 8) {
            throw new IllegalArgumentException("usage: --orekit-data DIR --tle FILE --end UTC --out FILE");
        }
        Map<String, String> options = new HashMap<>();
        for (int index = 0; index < arguments.length; index += 2) {
            String key = arguments[index];
            if (!(key.equals("--orekit-data") || key.equals("--tle") || key.equals("--end")
                    || key.equals("--out")) || arguments[index + 1].isBlank()
                    || options.putIfAbsent(key, arguments[index + 1]) != null) {
                throw new IllegalArgumentException("invalid reconstruction argument");
            }
        }
        return options;
    }
}
