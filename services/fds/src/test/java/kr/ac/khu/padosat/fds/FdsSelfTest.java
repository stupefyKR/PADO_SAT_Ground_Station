package kr.ac.khu.padosat.fds;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.BoundedPropagator;
import org.orekit.propagation.analytical.tle.TLEPropagator;

/** Dependency-free integration checks using a published Vallado SGP4 case. */
public final class FdsSelfTest {
    private FdsSelfTest() { }

    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 3) {
            throw new IllegalArgumentException("expected fixture, Orekit data and scratch directories");
        }
        Path fixture = Path.of(arguments[0]);
        OrbitModel.configureOfflineData(Path.of(arguments[1]));
        Path temporary = Files.createTempDirectory(Path.of(arguments[2]), "fds-tests-");
        try {
            validateInput(fixture, temporary);
            validateSourcePriority(fixture, temporary);
            validateSgp4Reference(fixture);
            validateProducts(fixture, temporary);
            validateNumerical72h(fixture, temporary);
            System.out.println("FDS tests passed");
        } finally {
            try (var files = Files.walk(temporary)) {
                for (Path path : files.sorted(Comparator.reverseOrder()).toList()) {
                    Files.deleteIfExists(path);
                }
            }
        }
    }

    private static void validateInput(Path fixture, Path temporary) throws IOException {
        Path config = fixture.resolve("test-config.properties");
        check(FdsConfig.read(config).horizonHours() == 24, "fixture horizon");
        Path badConfig = temporary.resolve("bad-config.properties");
        Files.writeString(badConfig, Files.readString(config).replace("horizon_hours=24",
                "horizon_hours=73"));
        rejected(() -> FdsConfig.read(badConfig), "73h horizon");
        Files.writeString(badConfig, Files.readString(config) + "\nunknown_setting=true\n");
        rejected(() -> FdsConfig.read(badConfig), "unknown configuration");
        Path badGnss = temporary.resolve("bad-gnss.properties");
        String gnss = Files.readString(fixture.resolve("test-gnss.properties"));
        Files.writeString(badGnss, gnss.replace("position_valid=true", "position_valid=false"));
        rejected(() -> OrbitInput.readGnss(badGnss), "invalid GNSS status");
        Files.writeString(badGnss, gnss.replace("frame=ITRF", "frame=GCRF"));
        rejected(() -> OrbitInput.readGnss(badGnss), "unsupported GNSS frame");
        Files.writeString(badGnss, gnss + "\nframe=ITRF\n");
        rejected(() -> OrbitInput.readGnss(badGnss), "duplicate GNSS field");
        Path badTle = temporary.resolve("bad.tle");
        String tle = Files.readString(fixture.resolve("vallado-00005.tle"));
        Files.writeString(badTle, tle.replace("  4753", "  4754"));
        rejected(() -> OrbitInput.readTle(badTle), "TLE checksum");
    }

    private static void validateSourcePriority(Path fixture, Path temporary) throws IOException {
        Path tle = fixture.resolve("vallado-00005.tle");
        Path gnss = fixture.resolve("test-gnss.properties");
        Instant start = Instant.parse("2000-06-27T19:00:00Z");
        check(OrbitInput.select(tle, gnss, start, 5).source().equals("GNSS"), "fresh GNSS priority");
        var missing = OrbitInput.select(tle, null, start, 5);
        check(missing.source().equals("TLE") && missing.reason().equals("GNSS_MISSING"),
                "missing GNSS fallback");
        Path laterTle = temporary.resolve("later.tle");
        List<String> lines = Files.readAllLines(tle, StandardCharsets.US_ASCII);
        String changedEpoch = lines.get(0).replace("00179.78495062", "00182.78495062");
        Files.writeString(laterTle, checksum(changedEpoch) + "\n" + lines.get(1) + "\n");
        Instant later = start.plusSeconds(72L * 3600L);
        var stale = OrbitInput.select(laterTle, gnss, later, 5);
        check(stale.source().equals("TLE") && stale.reason().equals("GNSS_STALE_72H") &&
                stale.lastGnssEpoch().equals(start),
                "GNSS 72h boundary fallback");
        rejected(() -> OrbitInput.select(tle, gnss, start.minusSeconds(1), 5), "future GNSS");
        rejected(() -> OrbitInput.select(null, null, start, 5), "missing all sources");
        rejected(() -> OrbitInput.select(tle, null, start, 6), "wrong TLE satellite");
        rejected(() -> OrbitInput.select(tle, gnss, start, 6), "wrong GNSS satellite");
    }

    private static String checksum(String line) {
        String prefix = line.substring(0, 68);
        int total = 0;
        for (int index = 0; index < prefix.length(); index++) {
            char value = prefix.charAt(index);
            total += Character.isDigit(value) ? value - '0' : value == '-' ? 1 : 0;
        }
        return prefix + (total % 10);
    }

    private static void validateSgp4Reference(Path fixture) throws IOException {
        var tle = OrbitInput.readTle(fixture.resolve("vallado-00005.tle"));
        var sgp4 = TLEPropagator.selectExtrapolator(tle);
        var observed = sgp4.getPVCoordinates(tle.getDate().shiftedBy(72.0 * 3600.0),
                FramesFactory.getTEME());
        // Vallado et al. AIAA-2006-6753, Appendix C, 3-day TEME vector (m).
        Vector3D published = new Vector3D(-9060473.73569, 4658709.52502, 813686.73153);
        check(observed.getPosition().distance(published) < 100.0,
                "SGP4 position diverges from Vallado reference");
    }

    private static void validateProducts(Path fixture, Path temporary) throws Exception {
        FdsConfig config = FdsConfig.read(fixture.resolve("test-config.properties"));
        Path tle = fixture.resolve("vallado-00005.tle");
        var selected = OrbitInput.select(tle, null, config.start(), config.noradId());
        BoundedPropagator orbit = OrbitModel.generate(OrbitModel.create(selected, config),
                OrbitInput.date(config.start()).shiftedBy(24.0 * 3600.0));
        Path output = temporary.resolve("products");
        FdsOutput.write(output, config, selected, tle, orbit);
        check(Files.size(output.resolve("contact_schedule.csv")) > 128, "empty passes");
        check(Files.readAllLines(output.resolve("contact_schedule.csv")).stream()
                .anyMatch(line -> line.contains(",yonsei,")), "Yonsei passes absent");
        check(Files.readAllLines(output.resolve("contact_schedule.csv")).stream()
                .anyMatch(line -> line.contains(",kari,")), "KARI passes absent");
        check(Files.readAllLines(output.resolve("orbit_ephemeris.csv")).size() == 1442,
                "24h ephemeris cadence");
        for (String line : Files.readAllLines(output.resolve("contact_schedule.csv")).subList(1,
                Files.readAllLines(output.resolve("contact_schedule.csv")).size())) {
            String[] fields = line.split(",");
            Instant aos = Instant.parse(fields[4]);
            Instant los = Instant.parse(fields[5]);
            Instant peak = Instant.parse(fields[6]);
            check(!peak.isBefore(aos) && !peak.isAfter(los) && aos.isBefore(los),
                    "invalid pass boundaries");
        }
        List<String> profile = Files.readAllLines(output.resolve("pass_information.csv"));
        check(profile.size() > 1, "no Doppler samples");
        for (String line : profile.subList(1, profile.size())) {
            String[] fields = line.split(",");
            double rate = Double.parseDouble(fields[8]);
            double shift = Double.parseDouble(fields[10]);
            check(Math.abs(shift + Double.parseDouble(fields[9]) * rate / 299792458.0) < 0.001,
                    "Doppler sign or magnitude");
            check(fields[12].equals("true"), "illustrative carrier must be marked");
        }
        check(Files.readString(output.resolve("run.properties")).contains("source=TLE"),
                "source metadata absent");
        for (String name : List.of("contact_schedule.csv", "pass_information.csv",
                "orbit_ephemeris.csv")) {
            String manifest = Files.readString(output.resolve(name + ".manifest.json"));
            check(manifest.contains(FdsOutput.sha256(output.resolve(name))), "manifest hash");
        }
        rejected(() -> FdsOutput.write(output, config, selected, tle, orbit),
                "existing output must not be replaced");
    }

    private static void validateNumerical72h(Path fixture, Path temporary) throws Exception {
        Path configPath = temporary.resolve("72h.properties");
        Files.writeString(configPath, Files.readString(fixture.resolve("test-config.properties"))
                .replace("horizon_hours=24", "horizon_hours=72"));
        FdsConfig config = FdsConfig.read(configPath);
        Path gnss = fixture.resolve("test-gnss.properties");
        var selected = OrbitInput.select(fixture.resolve("vallado-00005.tle"), gnss,
                config.start(), config.noradId());
        check(selected.source().equals("GNSS"), "GNSS not selected");
        BoundedPropagator orbit = OrbitModel.generate(OrbitModel.create(selected, config),
                OrbitInput.date(config.start()).shiftedBy(72.0 * 3600.0));
        Path output = temporary.resolve("numerical-products");
        FdsOutput.write(output, config, selected, gnss, orbit);
        check(Files.readAllLines(output.resolve("orbit_ephemeris.csv")).size() == 4322,
                "72h ephemeris cadence");
        check(Files.readString(output.resolve("run.properties")).contains("source=GNSS"),
                "GNSS source metadata absent");
        check(Files.readAllLines(output.resolve("contact_schedule.csv")).size() > 2,
                "72h GNSS passes absent");
    }

    private static void check(boolean success, String message) {
        if (!success) {
            throw new AssertionError(message);
        }
    }

    private static void rejected(Checked action, String message) {
        try {
            action.run();
        } catch (IllegalArgumentException | IOException expected) {
            return;
        }
        throw new AssertionError("expected rejection: " + message);
    }

    private interface Checked { void run() throws IOException; }
}
