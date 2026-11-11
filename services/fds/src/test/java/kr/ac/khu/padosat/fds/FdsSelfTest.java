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
        Files.writeString(badConfig, Files.readString(config) + "\nmaneuver_delta_v_mps=1\n");
        rejected(() -> FdsConfig.read(badConfig), "FDS excludes orbit maneuver input");
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
        String viewer = Files.readString(output.resolve("orbit_view.json"));
        check(viewer.contains("\"schema\":\"fds.orbit-view.v1\"") &&
                viewer.contains("\"contact_schedule_sha256\":\"" +
                        FdsOutput.sha256(output.resolve("contact_schedule.csv")) + "\"") &&
                viewer.contains("\"orbital_period_s\":") &&
                viewer.contains("\"id\":\"yonsei\"") &&
                viewer.contains("\"id\":\"kari\"") &&
                viewer.contains("\"samples\":[[\""), "orbit viewer product absent");
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
        List<String> pointing = Files.readAllLines(output.resolve("antenna_pointing.csv"));
        List<String> requests = Files.readAllLines(output.resolve("pass_request.csv"));
        List<String> schedule = Files.readAllLines(output.resolve("contact_schedule.csv"));
        check(pointing.size() == profile.size(), "N08 pointing cadence differs from N15 profile");
        check(requests.size() == schedule.size(), "N08 pass request count differs from N13");
        for (int row = 1; row < requests.size(); row++) {
            String[] request = requests.get(row).split(",");
            String[] contact = schedule.get(row).split(",");
            check(request.length == 11 && request[0].equals("fds.pass-request.draft.v1") &&
                    request[1].equals(contact[1]) && request[2].equals(contact[2]) &&
                    request[3].equals(contact[3]) && request[4].equals(contact[4]) &&
                    request[5].equals(contact[5]) && request[7].equals(contact[9]) &&
                    request[8].equals(contact[10]) &&
                    request[9].equals("PRELIMINARY_NOT_FOR_OPERATIONS") &&
                    request[10].equals("UNVERIFIED_TEST_VALUES"),
                    "N08 request lost contact window or draft status");
        }
        for (int row = 1; row < pointing.size(); row++) {
            String[] sample = pointing.get(row).split(",");
            String[] reference = profile.get(row).split(",");
            check(sample.length == 9 && sample[0].equals("fds.antenna-pointing.draft.v1") &&
                    sample[1].equals(reference[1]) && sample[2].equals(reference[2]) &&
                    sample[3].equals(reference[3]) && sample[4].equals(reference[4]) &&
                    sample[5].equals(reference[5]) && sample[6].equals(reference[6]) &&
                    sample[7].equals("PRELIMINARY_NOT_FOR_OPERATIONS") &&
                    sample[8].equals("UNVERIFIED_TEST_VALUES"),
                    "N08 pointing is not the calculated PASS profile");
        }
        for (String line : profile.subList(1, profile.size())) {
            String[] fields = line.split(",");
            double rate = Double.parseDouble(fields[8]);
            double shift = Double.parseDouble(fields[10]);
            check(Math.abs(shift + Double.parseDouble(fields[9]) * rate / 299792458.0) < 0.001,
                    "Doppler sign or magnitude");
            check(fields[12].equals("true"), "illustrative carrier must be marked");
        }
        validateDopplerProducts(output, config.profileStepSeconds());
        check(Files.readString(output.resolve("run.properties")).contains("source=TLE"),
                "source metadata absent");
        check(Files.readString(output.resolve("run.properties")).contains("maneuver_model=NONE"),
                "unmodeled maneuvers must be explicit");
        for (String name : List.of("contact_schedule.csv", "pass_information.csv",
                "orbit_ephemeris.csv", "pass_request.csv", "antenna_pointing.csv")) {
            String manifest = Files.readString(output.resolve(name + ".manifest.json"));
            check(manifest.contains(FdsOutput.sha256(output.resolve(name))), "manifest hash");
            if (name.equals("pass_request.csv") || name.equals("antenna_pointing.csv")) {
                check(manifest.contains("\"to\":\"dgs-gs\""), "N08 destination");
            }
        }
        rejected(() -> FdsOutput.write(output, config, selected, tle, orbit),
                "existing output must not be replaced");
        Path failed = temporary.resolve("failed-products");
        rejected(() -> FdsOutput.write(failed, config, selected,
                temporary.resolve("missing-input.tle"), orbit), "missing input hash");
        check(!Files.exists(failed), "failed output was published");
        try (var children = Files.list(temporary)) {
            check(children.noneMatch(path -> path.getFileName().toString().startsWith("fds-staging-")),
                    "failed output left nested Doppler staging files");
        }
    }

    private static void validateDopplerProducts(Path output, int stepSeconds) throws IOException {
        List<String> aggregate = Files.readAllLines(output.resolve("pass_information.csv"));
        List<String> schedule = Files.readAllLines(output.resolve("contact_schedule.csv"));
        Path directory = output.resolve("doppler_profiles");
        try (var files = Files.list(directory)) {
            check(files.count() == schedule.size() - 1, "per-pass Doppler count differs from N13");
        }
        for (String contactLine : schedule.subList(1, schedule.size())) {
            String[] contact = contactLine.split(",");
            String passId = contact[3];
            List<String> expectedRows = aggregate.stream().skip(1)
                    .filter(line -> line.split(",")[3].equals(passId)).toList();
            check(!expectedRows.isEmpty(), "scheduled pass has no Doppler samples");
            Path csv = directory.resolve(passId + ".csv");
            String expected = aggregate.get(0) + "\n" +
                    String.join(System.lineSeparator(), expectedRows) + System.lineSeparator();
            check(Files.readString(csv).equals(expected),
                    "Doppler CSV differs from calculated aggregate for " + passId);
            check(expectedRows.get(0).split(",")[4].equals(contact[4]) &&
                    expectedRows.get(expectedRows.size() - 1).split(",")[4].equals(contact[5]),
                    "Doppler profile omits AOS or LOS");
            Instant previous = null;
            for (String row : expectedRows) {
                String[] values = row.split(",");
                check(values.length == 13 && values[0].equals("fds.pass-information.v1") &&
                        values[1].equals(contact[1]) && values[2].equals(contact[2]),
                        "Doppler station or schema mismatch");
                Instant stamp = Instant.parse(values[4]);
                if (previous != null) {
                    long interval = stamp.toEpochMilli() - previous.toEpochMilli();
                    check(interval > 0 && interval <= stepSeconds * 1000L + 1L,
                            "Doppler sample order or cadence");
                }
                previous = stamp;
                for (int column = 5; column <= 11; column++) {
                    check(Double.isFinite(Double.parseDouble(values[column])),
                            "nonfinite Doppler profile value");
                }
                double rate = Double.parseDouble(values[8]);
                double carrier = Double.parseDouble(values[9]);
                double shift = Double.parseDouble(values[10]);
                double received = Double.parseDouble(values[11]);
                check(Math.abs(shift + carrier * rate / 299792458.0) < 0.001 &&
                        Math.abs(received - carrier - shift) < 0.001,
                        "Doppler frequency formula");
                check((rate >= -0.001 || shift > 0.0) && (rate <= 0.001 || shift < 0.0),
                        "Doppler approach/recession sign");
            }
        }
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
        check(Files.size(output.resolve("orbit_view.json")) > 100000,
                "72h orbit viewer product absent");
        check(Files.readString(output.resolve("run.properties")).contains("source=GNSS"),
                "GNSS source metadata absent");
        check(Files.readAllLines(output.resolve("contact_schedule.csv")).size() > 2,
                "72h GNSS passes absent");
        validateDopplerProducts(output, config.profileStepSeconds());
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
