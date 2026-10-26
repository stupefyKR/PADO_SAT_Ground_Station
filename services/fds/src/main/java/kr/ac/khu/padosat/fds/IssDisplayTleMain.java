package kr.ac.khu.padosat.fds;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.orekit.propagation.analytical.tle.TLE;
import org.orekit.propagation.analytical.tle.TLEPropagator;
import org.orekit.propagation.analytical.tle.generation.FixedPointTleGenerationAlgorithm;

/**
 * TEST_ONLY: extrapolate a fixed 2026 ISS TLE and refit its state to a recent epoch.
 *
 * <p>This creates a fictional display trajectory. It is not a current ISS TLE,
 * observed state, or a valid input for real contact planning.</p>
 */
public final class IssDisplayTleMain {
    public static final int SATELLITE_NUMBER = 25544;
    public static final String REFERENCE_LINE1 =
            "1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9990";
    public static final String REFERENCE_LINE2 =
            "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551";

    private IssDisplayTleMain() { }

    public static void main(String[] arguments) throws IOException {
        Map<String, String> options = parseOptions(arguments);
        OrbitModel.configureOfflineData(Path.of(options.get("--orekit-data")));
        TLE reference = readReference(Path.of(options.get("--reference")));
        Instant epoch = parseEpoch(options.get("--epoch"));
        TLE refreshed = at(reference, epoch);
        write(Path.of(options.get("--out")), refreshed);
        System.out.println("TEST_ONLY fictional ISS fixed-reference extrapolation at "
                + epoch + ": " + options.get("--out"));
    }

    public static TLE readReference(Path path) throws IOException {
        List<String> lines = Files.readAllLines(path, StandardCharsets.US_ASCII);
        if (lines.size() != 2 || !REFERENCE_LINE1.equals(lines.get(0))
                || !REFERENCE_LINE2.equals(lines.get(1)) ||
                !TLE.isFormatOK(lines.get(0), lines.get(1))) {
            throw new IllegalArgumentException("ISS reference differs from the fixed test fixture");
        }
        return new TLE(lines.get(0), lines.get(1));
    }

    public static TLE at(TLE reference, Instant epoch) {
        if (reference.getSatelliteNumber() != SATELLITE_NUMBER) {
            throw new IllegalArgumentException("ISS fixture satellite number mismatch");
        }
        var date = OrbitInput.date(epoch);
        var state = TLEPropagator.selectExtrapolator(reference).propagate(date);
        TLE refreshed = new FixedPointTleGenerationAlgorithm().generate(state, reference);
        if (refreshed.getSatelliteNumber() != SATELLITE_NUMBER
                || Math.abs(refreshed.getDate().durationFrom(date)) > 0.001) {
            throw new IllegalArgumentException("ISS display TLE refit failed");
        }
        return refreshed;
    }

    private static Instant parseEpoch(String text) {
        if (!text.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) {
            throw new IllegalArgumentException("--epoch must be whole-second UTC");
        }
        return Instant.parse(text);
    }

    private static Map<String, String> parseOptions(String[] arguments) {
        if (arguments.length != 8) {
            throw new IllegalArgumentException(
                    "usage: --orekit-data DIR --reference FILE --epoch UTC_ISO --out FILE");
        }
        Map<String, String> options = new HashMap<>();
        for (int index = 0; index < arguments.length; index += 2) {
            String name = arguments[index];
            if (!(name.equals("--orekit-data") || name.equals("--reference")
                    || name.equals("--epoch") || name.equals("--out"))
                    || arguments[index + 1].isBlank()
                    || options.putIfAbsent(name, arguments[index + 1]) != null) {
                throw new IllegalArgumentException("invalid ISS display TLE argument: " + name);
            }
        }
        return options;
    }

    private static void write(Path destination, TLE tle) throws IOException {
        Path absolute = destination.toAbsolutePath();
        Path directory = absolute.getParent();
        Files.createDirectories(directory);
        Path temporary = Files.createTempFile(directory, "iss-display-tle-", ".tmp");
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
